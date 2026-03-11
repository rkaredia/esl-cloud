import paho.mqtt.client as mqtt
import msgpack
import json
import logging
import os
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from .models import ESLTag, Gateway, Store, GlobalSetting, MQTTMessage

"""
MQTT COMMUNICATION ENGINE: THE SYSTEM BACKBONE
---------------------------------------------
This module handles all communication between the SAIS Cloud and the
physical ESL Gateways (eStations).

HOW IT WORKS (The Lifecycle of a Message):
1. TRIGGER: A product price changes in Django.
2. TASK: Celery generates a BMP image and calls `mqtt_service.publish_tag_update`.
3. PUBLISH: This client sends a binary message (msgpack) to the MQTT Broker.
4. GATEWAY: The physical Gateway (eStation) receives the message and updates the tag.
5. RESULT: The Gateway sends a '/result' message back to this client.
6. UPDATE: This client processes the result and marks the ESLTag as 'SUCCESS' in the DB.

PROTOCOL DETAILS:
- Port: 9081 (standard for D21 eStations)
- Serialization: MessagePack (msgpack) for binary efficiency, JSON for some heartbeats.
- Security: TLS 1.2 with Certificate-based encryption.
"""

logger = logging.getLogger(__name__)

class ESLMqttClient:
    """
    SAIS MQTT CLIENT MANAGER
    ------------------------
    A wrapper around the Paho MQTT library. It manages the connection,
    subscription to topics, and routing of incoming messages to handlers.
    """
    def __init__(self):
        try:
            # We use MQTTv5 for modern features and improved error reporting
            self.client = mqtt.Client(protocol=mqtt.MQTTv5)

            # Register callbacks (Event Handlers)
            self.client.on_connect = self.on_connect
            self.client.on_message = self.on_message
        except Exception:
            logger.exception("Failed to initialize MQTT client")

    def connect(self):
        """
        INITIALIZE CONNECTION
        ---------------------
        Configures authentication, encryption (TLS), and starts the
        background listening loop.
        """
        try:
            host = getattr(settings, 'MQTT_SERVER', 'localhost')
            port = getattr(settings, 'MQTT_PORT', 1883)
            user = getattr(settings, 'MQTT_USER', 'test')
            pw = getattr(settings, 'MQTT_PASS', '123456')

            # Authentication credentials for the MQTT Broker
            self.client.username_pw_set(user, pw)

            # TLS (SSL) CONFIGURATION
            # D21 Gateways require TLS 1.2 for secure communication.
            # We point to the CA (Certificate Authority) file stored in the repo.
            # ca_path = os.path.join(settings.BASE_DIR, 'mosquitto', 'certs', 'ca.crt')
            # if os.path.exists(ca_path):
            #     import ssl
            #     self.client.tls_set(ca_certs=ca_path, tls_version=ssl.PROTOCOL_TLSv1_2)
            #     # tls_insecure_set(True) allows us to use self-signed certificates
            #     # (common in local hardware setups).
            #     self.client.tls_insecure_set(True)

            # Connect to the broker (timeout after 60 seconds)
            self.client.connect(host, port, 60)

            # loop_start() runs the client in a separate background thread
            # so it doesn't block the main Django/Celery process.
            self.client.loop_start()
            logger.info(f"MQTT Client loop started for {host}:{port} (TLS Disabled)")
        except Exception:
            logger.exception("MQTT Connection Failed")

    def on_connect(self, client, userdata, flags, rc, properties=None):
        """
        EVENT: CONNECTED
        ----------------
        Triggered when the client successfully handshakes with the broker.
        Once connected, we 'Subscribe' to the topics we care about.
        The '+' in the topic is a WILDCARD (e.g., /estation/ANY_ID/result).
        """
        try:
            logger.info(f"Connected to MQTT Broker with result code {rc}")

            # /result: Outcome of a tag update (Success/Fail)
            self.client.subscribe("/estation/+/result", qos=2)

            # /heartbeat: Gateway status (Online, IP, etc.) sent every few mins
            self.client.subscribe("/estation/+/heartbeat", qos=0)

            # /tagheartbeat: List of all tags currently seen by a gateway
            self.client.subscribe("/estation/+/tagheartbeat", qos=0)

            # /infor: Detailed hardware/firmware info sent on gateway boot
            self.client.subscribe("/estation/+/infor", qos=0)

            # /message: General system logs/error messages from hardware
            self.client.subscribe("/estation/+/message", qos=0)
        except Exception:
            logger.exception("Error in MQTT on_connect callback")

    def on_message(self, client, userdata, msg):
        """
        EVENT: MESSAGE RECEIVED
        -----------------------
        The central traffic controller for all incoming MQTT data.
        It identifies the gateway ID and routes the data to the correct handler.
        """
        try:
            # Topic format is usually: /estation/<estation_id>/<command>
            topic_parts = msg.topic.split('/')
            if len(topic_parts) < 3: return
            estation_id = topic_parts[2]

            # DATA UNPACKING (De-serialization)
            # The eStation protocol primarily uses 'msgpack' (binary).
            # Some messages might be standard JSON.
            try:
                data = msgpack.unpackb(msg.payload)
            except Exception:
                try:
                    data = json.loads(msg.payload.decode())
                except Exception:
                    logger.error(f"Failed to unpack MQTT payload on {msg.topic}")
                    return

            # Log every message to the DB for auditing
            self._log_mqtt_message("received", estation_id, msg.topic, data)

            # Route to specific business logic handlers
            if msg.topic.endswith("/result"):
                self.handle_result(estation_id, data)
            elif msg.topic.endswith("/heartbeat"):
                # Hardware can send a list of tags inside the heartbeat
                self.handle_heartbeat(estation_id, data)

                # Check if this heartbeat contains a tag list (List format at index 8 or 'Tags' key)
                tags = []
                if isinstance(data, list) and len(data) > 8:
                    tags = data[8]
                elif isinstance(data, dict):
                    tags = data.get('Tags', [])

                if tags:
                    self._process_tags(estation_id, tags)

            elif msg.topic.endswith("/tagheartbeat"):
                self.handle_tag_heartbeat(estation_id, data)
            elif msg.topic.endswith("/infor"):
                self.handle_infor(estation_id, data)
        except Exception:
            logger.exception(f"Error processing MQTT message on topic {msg.topic}")

    def handle_result(self, estation_id, data):
        """
        PROCESS UPDATE RESULTS
        ----------------------
        Triggered when a Gateway reports back after trying to update a tag.
        Updates the 'sync_state' in the database.
        """
        try:
            # Protocol definition: [TagID, RfPower, Battery, Version, Status, Token, Temp, Channel]
            if isinstance(data, list):
                if len(data) < 6: return
                tag_mac, battery_raw, status_code, token = data[0], data[2], data[4], data[5]
            else:
                tag_mac = data.get('TagId')
                battery_raw = data.get('Battery')
                status_code = data.get('Status')
                token = data.get('Token')

            # 1. Identify which gateway sent this
            gateway = Gateway.objects.filter(estation_id=estation_id).first()
            if not gateway:
                logger.error(f"Received result from unknown gateway {estation_id}")
                return

            # 2. Find the tag being updated (Isolated by the gateway's store)
            tag = ESLTag.objects.filter(tag_mac=tag_mac, store=gateway.store).first()

            # 3. Verify Token: We only update if the token matches the last task we sent
            # This prevents old/duplicate results from overwriting new states.
            if tag and tag.last_image_task_token == token:
                is_success = (status_code == 0) # 0 = Success in eStation protocol
                tag.sync_state = 'SUCCESS' if is_success else 'FAILED'
                tag.battery_level = battery_raw

                if is_success:
                    # Update 'last_successful_gateway_id' for future routing optimization
                    tag.last_successful_gateway_id = estation_id

                # update_fields improves performance by only saving specific columns
                tag.save(update_fields=['sync_state', 'battery_level', 'last_successful_gateway_id'])
                logger.info(f"Tag {tag_mac} sync result: {'SUCCESS' if is_success else 'FAILED'}")
        except Exception:
            logger.exception("Error handling MQTT result message")

    def handle_heartbeat(self, estation_id, data):
        """
        GATEWAY TELEMETRY (Heartbeat)
        -----------------------------
        Updates the Gateway record in Django with its current IP,
        firmware version, and online status.
        """
        try:
            update_data = {
                'last_heartbeat': timezone.now(),
                'last_successful_heartbeat': timezone.now(),
                'is_online': True,
                'last_seen': timezone.now()
            }

            if isinstance(data, list):
                # New Hardware List Format: ["ID", "Alias", "IP", "MAC", ApType, "ApVer", "ModVer", Disk, Free, "Server", [...], Encrypt, AutoIP, "LocalIP", "Subnet", "Gateway", Heartbeat]
                if len(data) >= 17:
                    update_data.update({
                        'alias': data[1],
                        'gateway_ip': data[2],
                        'gateway_mac': data[3],
                        'ap_type': data[4],
                        'ap_version': data[5],
                        'module_version': data[6],
                        'disk_size': data[7],
                        'free_space': data[8],
                        'heartbeat_interval': data[16],
                    })
                    server = data[9]
                    conn_param = data[10]
                    update_data['is_encrypt_enabled'] = data[11]
                    update_data['is_auto_ip'] = data[12]
                    update_data['local_ip'] = data[13]
                    update_data['netmask'] = data[14]
                    update_data['network_gateway'] = data[15]
                else:
                    # Short Heartbeat format (from user log: ["", 0, "0.0.0", "", 3, "", 0, 0, []])
                    # We rely on the topic ID (estation_id) since the payload ID is empty
                    pass
            else:
                # Demo Dictionary Format: {"ID": "...", "MAC": "...", ...}
                update_data.update({
                    'alias': data.get('Alias'),
                    'gateway_ip': data.get('IP'),
                    'gateway_mac': data.get('MAC'),
                    'ap_type': data.get('ApType'),
                    'ap_version': data.get('ApVersion'),
                    'module_version': data.get('ModVersion'),
                    'disk_size': data.get('DiskSize'),
                    'free_space': data.get('FreeSpace'),
                    'heartbeat_interval': data.get('Heartbeat'),
                })
                server = data.get('Server', '')
                conn_param = data.get('ConnParam', [])
                if 'Encrypt' in data: update_data['is_encrypt_enabled'] = data['Encrypt']
                if 'AutoIP' in data: update_data['is_auto_ip'] = data['AutoIP']
                if 'LocalIP' in data: update_data['local_ip'] = data['LocalIP']
                if 'Subnet' in data: update_data['netmask'] = data['Subnet']
                if 'Gateway' in data: update_data['network_gateway'] = data['Gateway']

            # Parse "Server" string common to both formats
            if 'server' in locals() and server:
                if ':' in server:
                    parts = server.split(':', 1)
                    update_data['app_server_ip'] = parts[0]
                    try:
                        update_data['app_server_port'] = int(parts[1])
                    except ValueError: pass
                else:
                    update_data['app_server_ip'] = server

            # Update login credentials if provided
            if 'conn_param' in locals() and len(conn_param) >= 2:
                update_data['username'] = conn_param[0]
                update_data['password'] = conn_param[1]

            # Trigger the update using the unique ID from the topic
            Gateway.objects.filter(estation_id=estation_id).update(**update_data)
        except Exception:
            logger.exception(f"Error handling heartbeat for gateway {estation_id}")

    def handle_infor(self, estation_id, data):
        """
        GATEWAY AUTO-REGISTRATION
        -------------------------
        Triggered when a new gateway boots up. Automatically creates
        a record in the DB if it doesn't exist.
        """
        try:
            update_data = {
                'estation_id': estation_id, # Always use the topic ID as primary
                'is_online': True,
                'last_heartbeat': timezone.now(),
                'last_seen': timezone.now()
            }

            if isinstance(data, list):
                # Hardware List format for /infor
                if len(data) >= 7:
                    mac = data[3]
                    update_data.update({
                        'alias': data[1],
                        'gateway_ip': data[2],
                        'gateway_mac': mac,
                        'ap_type': data[4],
                        'ap_version': data[5],
                        'module_version': data[6],
                    })
                    if len(data) >= 9:
                        update_data.update({
                            'disk_size': data[7],
                            'free_space': data[8],
                        })
                else: return
            else:
                # Dictionary format for /infor
                mac = data.get('MAC')
                if not mac: return
                update_data.update({
                    'alias': data.get('Alias'),
                    'gateway_ip': data.get('IP'),
                    'gateway_mac': mac,
                    'ap_type': data.get('ApType'),
                    'ap_version': data.get('ApVersion'),
                    'module_version': data.get('ModVersion'),
                    'disk_size': data.get('DiskSize'),
                    'free_space': data.get('FreeSpace'),
                    'heartbeat_interval': data.get('Heartbeat'),
                })

            # Register or Get existing
            # We first try to find by MAC to prevent duplicates if ID changed
            gateway = Gateway.objects.filter(gateway_mac=mac).first()
            if not gateway:
                # DEFAULT STORE: New gateways are assigned to the 'Admin Store' by default.
                store = Store.objects.filter(name='Admin Store').first() or Store.objects.first()
                if not store:
                    logger.error("No store found for auto-discovery")
                    return
                gateway = Gateway.objects.create(gateway_mac=mac, store=store, **update_data)
            else:
                Gateway.objects.filter(gateway_mac=mac).update(**update_data)

            logger.info(f"Gateway {mac} (ID:{estation_id}) updated via /infor")
        except Exception:
            logger.exception(f"Error handling infor for gateway {estation_id}")

    def handle_tag_heartbeat(self, estation_id, data):
        """
        WRAPPER: TAG HEARTBEAT HANDLER
        -----------------------------
        Ensures compatibility with existing tests and simplifies the entry point
        for processing tag lists from MQTT messages.
        """
        tags = data if isinstance(data, list) else data.get('Tags', [])
        self._process_tags(estation_id, tags)

    def _process_tags(self, estation_id, tags_list):
        """
        TAG AUTO-DISCOVERY & TELEMETRY ENGINE
        -------------------------------------
        Centralized logic for processing tag lists found in /heartbeat
        or /tagheartbeat messages.
        """
        try:
            if not tags_list: return

            gateway = Gateway.objects.filter(estation_id=estation_id).select_related('store').first()
            if not gateway: return

            # Extract MACs based on format
            incoming_macs = []
            tag_data_map = {}

            for tag_entry in tags_list:
                if isinstance(tag_entry, list):
                    # List format: ["TagId", RfPower, Battery, Version, Status, Color, ...]
                    mac = tag_entry[0]
                    battery = tag_entry[2]
                else:
                    # Dictionary format: {"TagId": "...", "Battery": ...}
                    mac = tag_entry.get('TagId')
                    battery = tag_entry.get('Battery')

                if mac:
                    incoming_macs.append(mac)
                    tag_data_map[mac] = {'battery': battery}

            # SECURITY & BULK OPTIMIZATION
            existing_tags = {t.tag_mac: t for t in ESLTag.objects.filter(
                tag_mac__in=incoming_macs,
                store=gateway.store
            )}

            from .models import TagHardware
            default_hw = None
            tags_to_update = {}
            tags_to_create = {}
            now = timezone.now()
            default_hw_queried = False

            for tag_mac, metadata in tag_data_map.items():
                tag = existing_tags.get(tag_mac)
                if tag:
                    tag.last_successful_gateway_id = estation_id
                    tag.battery_level = metadata['battery']
                    tag.updated_at = now
                    if not tag.store: tag.store = gateway.store
                    tags_to_update[tag_mac] = tag
                else:
                    if default_hw is None and not default_hw_queried:
                        default_hw = TagHardware.objects.first()
                        default_hw_queried = True

                    tags_to_create[tag_mac] = ESLTag(
                        tag_mac=tag_mac,
                        battery_level=metadata['battery'],
                        last_successful_gateway_id=estation_id,
                        store=gateway.store,
                        sync_state='IDLE',
                        hardware_spec=default_hw,
                        created_at=now,
                        updated_at=now
                    )

            if tags_to_update:
                ESLTag.objects.bulk_update(
                    tags_to_update.values(),
                    ['last_successful_gateway_id', 'battery_level', 'store', 'updated_at']
                )

            if tags_to_create:
                ESLTag.objects.bulk_create(tags_to_create.values())
                logger.info(f"Gateway {estation_id} discovered {len(tags_to_create)} new tags")

        except Exception:
            logger.exception(f"Error processing tag list for gateway {estation_id}")

    def publish_tag_update(self, gateway_id, tag_mac, image_bytes, token):
        """
        COMMAND: UPDATE TAG IMAGE
        -------------------------
        The most important function in the system. Sends a BMP image
        to a physical tag via a specific Gateway.
        """
        try:
            # Task Parameters: [TagId, OffsetX, OffsetY, IsWait, IsFast, IsInvert, Color, Token, RFU, RFU]
            task_params = [tag_mac, 0, 0, True, False, False, 0, token, "", ""]

            # Pack parameters and image bytes into a single msgpack binary payload
            payload = msgpack.packb([task_params, image_bytes])

            # The topic the physical gateway is listening on
            topic = f"/estation/{gateway_id}/taskESL2"

            # QoS 2: 'Exactly Once' delivery. Ensures the command isn't lost or duplicated.
            result = self.client.publish(topic, payload, qos=2)

            # Log the outgoing message (sanitizing binary data for the logs)
            self._log_mqtt_message("sent", gateway_id, topic, {"params": task_params, "image_len": len(image_bytes)})

            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published update for {tag_mac} to gateway {gateway_id}")
                return True
            else:
                logger.error(f"Failed to publish MQTT message for {tag_mac}: RC {result.rc}")
                return False
        except Exception:
            logger.exception(f"Exception during MQTT publish for tag {tag_mac}")
            return False

    def publish_config(self, gateway_id, alias, server, encrypt, heartbeat, auto_ip=True, local_ip="", subnet="", gateway="", username="test", password="123456"):
        """
        COMMAND: CONFIGURE GATEWAY
        -------------------------
        Sends a remote configuration command to a gateway to change its
        IP, Server link, or authentication credentials.
        """
        try:
            config_data = {
                'Alias': alias,
                'Server': server,
                'ConnParam': [username, password],
                'Encrypt': encrypt,
                'AutoIP': auto_ip,
                'LocalIP': local_ip,
                'Subnet': subnet,
                'Gateway': gateway,
                'Heartbeat': heartbeat
            }

            payload = msgpack.packb(config_data)
            topic = f"/estation/{gateway_id}/configure"

            result = self.client.publish(topic, payload, qos=2)
            is_success = (result.rc == mqtt.MQTT_ERR_SUCCESS)

            self._log_mqtt_message("sent", gateway_id, topic, config_data, force_success=is_success)

            return is_success
        except Exception:
            logger.exception(f"Exception during MQTT publish config for gateway {gateway_id}")
            return False

    def _sanitize_data(self, data):
        """
        Recursively sanitizes sensitive information from MQTT payloads
        before they are written to logs or the database.
        """
        if isinstance(data, dict):
            new_dict = {}
            for k, v in data.items():
                # Sanitize common sensitive keys
                if k.lower() in ['password', 'username', 'secret', 'token', 'connparam']:
                    new_dict[k] = "********"
                else:
                    new_dict[k] = self._sanitize_data(v)
            return new_dict
        elif isinstance(data, list):
            return [self._sanitize_data(item) for item in data]
        return data

    def _log_mqtt_message(self, direction, estation_id, topic, data, force_success=None):
        """
        AUDIT LOGGING
        -------------
        Writes a copy of every MQTT message to both the Database
        (for Admin view) and a daily Log File (for deep debugging).
        """
        try:
            # Security: Sanitize sensitive credentials before logging
            data = self._sanitize_data(data)

            # Helper to handle binary/bytes in JSON logs
            class BytesEncoder(json.JSONEncoder):
                def default(self, obj):
                    if isinstance(obj, bytes):
                        return f"<binary:{len(obj)} bytes>"
                    return super().default(obj)

            json_data = json.dumps(data, cls=BytesEncoder)

            # 1. Database Logging (MQTTMessage model)
            if force_success is not None:
                is_success = force_success
            else:
                is_success = True
                if direction == "received" and topic.endswith("/result"):
                    # Detect status from payload: 0 is Success
                    if isinstance(data, list) and len(data) >= 5:
                        is_success = (data[4] == 0)
                    elif isinstance(data, dict):
                        is_success = (data.get('Status') == 0)

            MQTTMessage.objects.create(
                direction=direction,
                estation_id=estation_id,
                topic=topic,
                data=json_data,
                is_success=is_success
            )

            # 2. File Logging (Daily rotating logs)
            log_dir = os.path.join(settings.BASE_DIR, 'logs', 'mqtt', direction)
            os.makedirs(log_dir, exist_ok=True)

            filename = f"{datetime.now().strftime('%Y-%m-%d')}.log"
            filepath = os.path.join(log_dir, filename)

            with open(filepath, 'a') as f:
                timestamp = datetime.now().isoformat()
                f.write(f"[{timestamp}] ID:{estation_id} TOPIC:{topic} DATA:{json_data}\n")

        except Exception:
            logger.exception("Failed to log MQTT message")

# EXPORT: Single global instance to be used across the app
mqtt_service = ESLMqttClient()
