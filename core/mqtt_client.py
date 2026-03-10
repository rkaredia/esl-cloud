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
            ca_path = os.path.join(settings.BASE_DIR, 'mosquitto', 'certs', 'ca.crt')
            if os.path.exists(ca_path):
                import ssl
                self.client.tls_set(ca_certs=ca_path, tls_version=ssl.PROTOCOL_TLSv1_2)
                # tls_insecure_set(True) allows us to use self-signed certificates
                # (common in local hardware setups).
                self.client.tls_insecure_set(True)

            # Connect to the broker (timeout after 60 seconds)
            self.client.connect(host, port, 60)

            # loop_start() runs the client in a separate background thread
            # so it doesn't block the main Django/Celery process.
            self.client.loop_start()
            logger.info(f"MQTT Client loop started for {host}:{port} (TLS Enabled)")
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
                self.handle_heartbeat(estation_id, data)
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
            gateway_id = data.get('ID', estation_id)
            mac = data.get('MAC')

            # Prepare update dictionary for bulk update performance
            update_data = {
                'alias': data.get('Alias'),
                'gateway_ip': data.get('IP'),
                'gateway_mac': mac,
                'ap_type': data.get('ApType'),
                'ap_version': data.get('ApVersion'),
                'module_version': data.get('ModVersion'),
                'disk_size': data.get('DiskSize'),
                'free_space': data.get('FreeSpace'),
                'heartbeat_interval': data.get('Heartbeat'),
                'last_heartbeat': timezone.now(),
                'last_successful_heartbeat': timezone.now(),
                'is_online': True,
                'last_seen': timezone.now()
            }

            # Parse "Server" string (e.g., "192.168.1.92:9081")
            server = data.get('Server', '')
            if server:
                if ':' in server:
                    parts = server.split(':', 1)
                    update_data['app_server_ip'] = parts[0]
                    try:
                        update_data['app_server_port'] = int(parts[1])
                    except ValueError: pass
                else:
                    update_data['app_server_ip'] = server

            # Update login credentials if provided
            conn_param = data.get('ConnParam', [])
            if len(conn_param) >= 2:
                update_data['username'] = conn_param[0]
                update_data['password'] = conn_param[1]

            # Network Settings
            if 'Encrypt' in data: update_data['is_encrypt_enabled'] = data['Encrypt']
            if 'AutoIP' in data: update_data['is_auto_ip'] = data['AutoIP']
            if 'LocalIP' in data: update_data['local_ip'] = data['LocalIP']
            if 'Subnet' in data: update_data['netmask'] = data['Subnet']
            if 'Gateway' in data: update_data['network_gateway'] = data['Gateway']

            # Trigger the update
            Gateway.objects.filter(estation_id=gateway_id).update(**update_data)
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
            mac = data.get('MAC')
            if not mac: return

            # DEFAULT STORE: New gateways are assigned to the 'Admin Store' by default.
            store = Store.objects.filter(name='Admin Store').first() or Store.objects.first()
            if not store:
                logger.error("No store found for auto-discovery")
                return

            update_data = {
                'estation_id': data.get('ID', estation_id),
                'alias': data.get('Alias'),
                'gateway_ip': data.get('IP'),
                'ap_type': data.get('ApType'),
                'ap_version': data.get('ApVersion'),
                'module_version': data.get('ModVersion'),
                'disk_size': data.get('DiskSize'),
                'free_space': data.get('FreeSpace'),
                'heartbeat_interval': data.get('Heartbeat'),
                'is_online': True,
                'last_heartbeat': timezone.now(),
                'last_seen': timezone.now()
            }

            # Register or Get existing
            gateway, created = Gateway.objects.get_or_create(
                gateway_mac=mac,
                defaults={**update_data, 'store': store}
            )

            if not created:
                Gateway.objects.filter(gateway_mac=mac).update(**update_data)

            logger.info(f"Gateway {mac} (ID:{estation_id}) {'registered' if created else 'updated'} via /infor")
        except Exception:
            logger.exception(f"Error handling infor for gateway {estation_id}")

    def handle_tag_heartbeat(self, estation_id, data):
        """
        TAG AUTO-DISCOVERY & TELEMETRY
        ------------------------------
        Gateways periodically send a list of every tag they can see.
        We use this to:
        1. Auto-create new tags in the DB (Zero-Touch Provisioning).
        2. Update battery levels for all tags.
        3. Track which gateway is currently the best link for a tag.
        """
        try:
            tags_list = data.get('Tags', [])
            if not tags_list: return

            gateway = Gateway.objects.filter(estation_id=estation_id).select_related('store').first()
            if not gateway: return

            incoming_macs = [t.get('TagId') for t in tags_list if t.get('TagId')]

            # SECURITY & BULK OPTIMIZATION: Only retrieve tags that belong to this gateway's store
            # to prevent cross-store data hijacking.
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

            for tag_data in tags_list:
                tag_mac = tag_data.get('TagId')
                battery = tag_data.get('Battery')
                if not tag_mac: continue

                tag = existing_tags.get(tag_mac)
                if tag:
                    # Update existing record logic
                    tag.last_successful_gateway_id = estation_id
                    tag.battery_level = battery
                    tag.updated_at = now
                    if not tag.store: tag.store = gateway.store
                    tags_to_update[tag_mac] = tag
                else:
                    # NEW TAG DISCOVERED: Create it automatically.
                    if default_hw is None and not default_hw_queried:
                        default_hw = TagHardware.objects.first()
                        default_hw_queried = True

                    tags_to_create[tag_mac] = ESLTag(
                        tag_mac=tag_mac,
                        battery_level=battery,
                        last_successful_gateway_id=estation_id,
                        store=gateway.store,
                        sync_state='IDLE',
                        hardware_spec=default_hw,
                        created_at=now,
                        updated_at=now
                    )

            # EFFICIENT WRITES: Use bulk_update and bulk_create to minimize DB round-trips.
            if tags_to_update:
                ESLTag.objects.bulk_update(
                    tags_to_update.values(),
                    ['last_successful_gateway_id', 'battery_level', 'store', 'updated_at']
                )

            if tags_to_create:
                ESLTag.objects.bulk_create(tags_to_create.values())
                logger.info(f"Auto-discovered {len(tags_to_create)} new tags via gateway {estation_id}")

        except Exception:
            logger.exception(f"Error handling tag heartbeat for gateway {estation_id}")

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

    def _log_mqtt_message(self, direction, estation_id, topic, data, force_success=None):
        """
        AUDIT LOGGING
        -------------
        Writes a copy of every MQTT message to both the Database
        (for Admin view) and a daily Log File (for deep debugging).
        """
        try:
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
