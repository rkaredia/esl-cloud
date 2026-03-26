import paho.mqtt.client as mqtt
import msgpack
import json
import logging
import os
import gzip
import io
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from .models import ESLTag, Gateway, Store, GlobalSetting, MQTTMessage

"""
MQTT COMMUNICATION ENGINE: THE SYSTEM BACKBONE
---------------------------------------------
This module handles all communication between the SAIS Cloud and the
physical ESL Gateways (eStations).

PROTOCOL DETAILS:
- Port: 9081 (standard for D21 eStations)
- Serialization: MessagePack (msgpack).
- Format: Supports both legacy Dictionary and new Hardware List formats.
"""

logger = logging.getLogger(__name__)

class ESLMqttClient:
    """
    SAIS MQTT CLIENT MANAGER
    ------------------------
    A wrapper around the Paho MQTT library.
    """
    def __init__(self):
        try:
            # Explicitly use Paho MQTT v2 API for compatibility with the latest library
            # Use default protocol (v3.1.1) for maximum hardware compatibility
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self.should_subscribe = False

            # Register callbacks (Event Handlers)
            self.client.on_connect = self.on_connect
            self.client.on_publish = self.on_publish
            self.client.on_subscribe = self.on_subscribe
            self.client.on_message = self.on_message
        except Exception:
            logger.exception("Failed to initialize MQTT client")

    def connect(self, subscribe=False):
        """
        INITIALIZE CONNECTION
        ---------------------
        Configures authentication and starts the background listening loop.
        """
        try:
            self.should_subscribe = subscribe
            host = getattr(settings, 'MQTT_SERVER', 'localhost')
            port = getattr(settings, 'MQTT_PORT', 1883)
            user = getattr(settings, 'MQTT_USER', 'test')
            pw = getattr(settings, 'MQTT_PASS', '123456')

            # Authentication credentials for the MQTT Broker
            self.client.username_pw_set(user, pw)

            # TLS is currently disabled as server.key is missing
            # ca_path = os.path.join(settings.BASE_DIR, 'mosquitto', 'certs', 'ca.crt')

            # Connect to the broker (timeout after 60 seconds)
            self.client.connect(host, port, 60)

            # loop_start() runs the client in a separate background thread
            self.client.loop_start()
            logger.info(f"MQTT Client loop started for {host}:{port} (TLS Disabled, Subscribe={subscribe})")
        except Exception:
            logger.exception("MQTT Connection Failed")

    def on_connect(self, client, userdata, flags, rc, properties=None):
        """
        EVENT: CONNECTED
        ----------------
        Triggered when the client successfully handshakes with the broker.
        """
        try:
            logger.info(f"Connected to MQTT Broker with result code {rc}")

            if self.should_subscribe:
                # Subscribe to all eStation topics with QoS 0 for maximum hardware compatibility
                self.client.subscribe("/estation/+/result", qos=0)
                self.client.subscribe("/estation/+/heartbeat", qos=0)
                self.client.subscribe("/estation/+/tagheartbeat", qos=0)
                self.client.subscribe("/estation/+/infor", qos=0)
                self.client.subscribe("/estation/+/message", qos=0)
                logger.info("MQTT Client subscribed to topics")
        except Exception:
            logger.exception("Error in MQTT on_connect callback")

    def on_publish(self, client, userdata, mid, reason_code=None, properties=None):
        logger.debug(f"MQTT Message {mid} published")

    def on_subscribe(self, client, userdata, mid, reason_code_list, properties=None):
        logger.debug(f"MQTT Subscription {mid} confirmed")

    def on_message(self, client, userdata, msg):
        """
        EVENT: MESSAGE RECEIVED
        -----------------------
        The central traffic controller for all incoming MQTT data.
        """
        try:
            # Diagnostic log for tracking all incoming data
            logger.debug(f"MQTT Data received on {msg.topic}: {msg.payload[:100]!r}...")

            # Topic format is usually: /estation/<estation_id>/<command>
            topic_parts = msg.topic.split('/')
            if len(topic_parts) < 3: return
            estation_id = topic_parts[2]

            # DATA UNPACKING (De-serialization)
            try:
                # Use raw=False to ensure strings are correctly decoded from bytes
                data = msgpack.unpackb(msg.payload, raw=False)
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

                # TAG HEARTBEAT DISABLED: We will enhance this in future updates
                # tags = []
                # if isinstance(data, list) and len(data) > 8:
                #     tags = data[8]
                # elif isinstance(data, dict):
                #     tags = data.get('Tags', [])

                # if tags:
                #     self._process_tags(estation_id, tags)

            elif msg.topic.endswith("/tagheartbeat"):
                # TAG HEARTBEAT DISABLED: We will enhance this in future updates
                # tags = data if isinstance(data, list) else data.get('Tags', [])
                # self._process_tags(estation_id, tags)
                pass
            elif msg.topic.endswith("/infor"):
                self.handle_infor(estation_id, data)
        except Exception:
            logger.exception(f"Error processing MQTT message on topic {msg.topic}")

    def _calculate_battery_percentage(self, voltage_raw):
        """
        CONVERT VOLTAGE TO PERCENTAGE
        -----------------------------
        ESL tags report raw voltage (e.g. 30 = 3.0V).
        Mapping: 3.0V (30) = 100%, 2.2V (22) = 0%.
        Value 0 is treated as abnormal and returns None.
        """
        try:
            val = int(voltage_raw)
            if val == 0: return None
            if val >= 30: return 100
            if val <= 22: return 0
            # Linear interpolation: (val - 22) / (30 - 22) * 100
            return int((val - 22) * 12.5)
        except (ValueError, TypeError):
            return 100

    def handle_result(self, estation_id, data):
        """
        PROCESS UPDATE RESULTS
        ----------------------
        Triggered when a Gateway reports back after trying to update a tag.
        Supports both single-tag and multi-tag result formats.
        """
        try:
            tag_results = []

            # 1. Identify format and normalize to a list of tag result objects
            if isinstance(data, list):
                # New Multi-tag format: [Port, WaitCount, SendCount, MessageCode, [TagResult1, ...]]
                if len(data) >= 5 and isinstance(data[4], list):
                    for tr in data[4]:
                        if isinstance(tr, list) and len(tr) >= 6:
                            tag_results.append({
                                'tag_mac': tr[0],
                                'battery_raw': tr[2],
                                'status_code': tr[4],
                                'token': tr[5]
                            })
                # Legacy/Single-tag format: [TagID, RfPower, Battery, Version, Status, Token, ...]
                else:
                    # Handle nested list wrapper if present
                    if len(data) == 1 and isinstance(data[0], list):
                        data = data[0]

                    if len(data) >= 6:
                        tag_results.append({
                            'tag_mac': data[0],
                            'battery_raw': data[2],
                            'status_code': data[4],
                            'token': data[5]
                        })
            elif isinstance(data, dict):
                tag_results.append({
                    'tag_mac': data.get('TagId'),
                    'battery_raw': data.get('Battery'),
                    'status_code': data.get('Status'),
                    'token': data.get('Token')
                })

            if not tag_results:
                return

            # 2. Identify which gateway sent this
            gateway = Gateway.objects.filter(estation_id__iexact=estation_id.strip()).first()
            if not gateway:
                logger.error(f"Received result from unknown gateway {estation_id}")
                return

            # 3. Process each tag result
            from django.db.models.functions import Upper, Replace
            from django.db.models import Value

            for res in tag_results:
                tag_mac = res['tag_mac']
                if not tag_mac: continue

                clean_mac = tag_mac.replace(':', '').upper()
                # Find the tag being updated (Isolated by the gateway's store)
                tag = ESLTag.objects.filter(store=gateway.store).annotate(
                    clean_db_mac=Upper(Replace('tag_mac', Value(':'), Value('')))
                ).filter(clean_db_mac=clean_mac).first()

                if not tag:
                    logger.warning(f"Result for unknown tag {tag_mac} in store {gateway.store}")
                    continue

                # Verify Token: We only update if the token matches the last task we sent
                if tag.last_image_task_token == res['token']:
                    status_code = res['status_code']
                    # SUCCESS codes: 1 and 128 per hardware observation (0 is failure)
                    is_success = (status_code == 1 or status_code == 128)

                    battery_pct = self._calculate_battery_percentage(res['battery_raw'])
                    update_fields = {
                        'sync_state': 'SUCCESS' if is_success else 'PUSH_FAILED',
                        'updated_at': timezone.now()
                    }
                    if battery_pct is not None:
                        update_fields['battery_level'] = battery_pct

                    if is_success:
                        update_fields['last_successful_gateway_id'] = estation_id

                    ESLTag.objects.filter(pk=tag.pk).update(**update_fields)
                    logger.info(f"Tag {tag_mac} sync result: {'SUCCESS' if is_success else 'FAILED'} (Batt: {update_fields['battery_level']}%)")
                else:
                    logger.warning(f"Token mismatch for tag {tag_mac}: Expected {tag.last_image_task_token}, got {res['token']}")

        except Exception:
            logger.exception("Error handling MQTT result message")

    def handle_tag_heartbeat(self, estation_id, data):
        """
        WRAPPER FOR TEST COMPATIBILITY
        ------------------------------
        Maintains backward compatibility with legacy tests that call
        this method directly. Routes to centralized tag processing.
        """
        tags = data if isinstance(data, list) else data.get('Tags', [])
        self._process_tags(estation_id, tags)

    def handle_heartbeat(self, estation_id, data):
        """
        GATEWAY TELEMETRY (Heartbeat)
        -----------------------------
        Updates the Gateway record with its current status.
        Supports 9-element list format.
        """
        try:
            # Message Code mapping
            ERROR_CODES = {
                5: "ModError: Abnormality of the communication module",
                6: "AppError: Abnormality of the main program",
                7: "Busy: The device is busy",
                8: "MaxLimit: Data queue limit reached",
                9: "InvalidTaskESL: Incorrect ESL task data",
                10: "InvalidTaskDSL: Incorrect DSL task data",
                11: "InvalidConfig: Incorrect configuration data",
                12: "InvalidOTA: Incorrect OTA data"
            }

            update_data = {
                'last_heartbeat': timezone.now(),
                'last_successful_heartbeat': timezone.now(),
                'is_online': 'ONLINE',
                'last_seen': timezone.now()
            }

            if isinstance(data, list):
                # 9-element list format: [AP ID, ConfigVer, BaseVer, BlueVer, MsgCode, MsgExt, Queued, Comm, Tags]
                if len(data) >= 8:
                    # If AP ID is provided and not empty, use it as estation_id
                    if data[0] and str(data[0]).strip():
                        estation_id = str(data[0]).strip()

                    msg_code = data[4]
                    update_data.update({
                        'ap_version': data[2],
                        'module_version': data[3],
                        'tags_queued_count': data[6],
                        'tags_comm_count': data[7],
                        'last_error_code': msg_code,
                    })

                    if msg_code in ERROR_CODES:
                        update_data['is_online'] = 'ERROR'
                        update_data['last_error_message'] = ERROR_CODES[msg_code]
                        update_data['last_error_timestamp'] = timezone.now()
                    elif msg_code in [1, 2, 3, 4]:
                        update_data['is_online'] = 'ONLINE'
                        update_data['last_error_message'] = None
                else:
                    logger.warning(f"Heartbeat for {estation_id} has unexpected length: {len(data)}")
                    return
            else:
                # Fallback to dictionary if needed, but primarily expecting list
                update_data.update({
                    'ap_version': data.get('ApVersion'),
                    'module_version': data.get('ModVersion'),
                    'tags_queued_count': data.get('Queued', 0),
                    'tags_comm_count': data.get('Comm', 0),
                })

            # Trigger the update (case-insensitive lookup)
            Gateway.objects.filter(estation_id__iexact=estation_id.strip()).update(**update_data)
        except Exception:
            logger.exception(f"Error handling heartbeat for gateway {estation_id}")

    def handle_tag_heartbeat(self, estation_id, data):
        """
        WRAPPER FOR TEST COMPATIBILITY
        -----------------------------
        Historically, this method handled tag lists. It now delegates
        to the centralized _process_tags engine.
        """
        tags = data if isinstance(data, list) else data.get('Tags', [])
        self._process_tags(estation_id, tags)

    def handle_infor(self, estation_id, data):
        """
        GATEWAY AUTO-REGISTRATION
        -------------------------
        Supports 17-element list format.
        """
        try:
            # Normalize ID for robust lookup
            clean_id = estation_id.strip()

            update_data = {
                'estation_id': clean_id,
                'is_online': 'ONLINE',
                'last_heartbeat': timezone.now(),
                'last_seen': timezone.now(),
                'last_error_message': None
            }

            if isinstance(data, list):
                # 17-element format: [ID, Nickname, LocalIP, MAC, ApType, MainVer, ModVer, Disk, Available, ServerIP, ConnParam, AutoIP, FixedIP, Mask, Gateway, ??, Heartbeat]
                if len(data) >= 17:
                    # Update estation_id if present in index 0
                    if data[0] and str(data[0]).strip():
                        clean_id = str(data[0]).strip()
                        update_data['estation_id'] = clean_id

                    mac = data[3]
                    update_data.update({
                        'alias': data[1],
                        'gateway_ip': data[2], # Gateway IP assigned by router
                        'gateway_mac': mac,
                        'ap_type': data[4],
                        'ap_version': data[5],
                        'module_version': data[6],
                        'disk_size': data[7],
                        'free_space': data[8],
                        'netmask': data[13],
                        'network_gateway': data[14],
                        'heartbeat_interval': int(data[16]) if data[16] else 15,
                        'is_auto_ip': data[11], # Always True per user requirement
                    })

                    server = data[9]
                    if server:
                        if ':' in server:
                            parts = server.split(':', 1)
                            update_data['app_server_ip'] = parts[0]
                            try:
                                update_data['app_server_port'] = int(parts[1])
                            except (ValueError, TypeError): pass
                        else:
                            update_data['app_server_ip'] = server

                    conn_param = data[10]
                    if isinstance(conn_param, list) and len(conn_param) >= 2:
                        update_data['username'] = conn_param[0]
                        update_data['password'] = conn_param[1]
                else:
                    logger.warning(f"Infor for {estation_id} has unexpected length: {len(data)}")
                    return
            else:
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
            # We check both MAC and ID (case-insensitive) to prevent IntegrityErrors
            from django.db.models import Q

            # Look for any record that might conflict with this hardware's MAC or reported ID
            conflicts = Gateway.objects.filter(Q(gateway_mac=mac) | Q(estation_id__iexact=clean_id))
            gateway = conflicts.first()

            if not gateway:
                store = Store.objects.filter(name='Admin Store').first() or Store.objects.first()
                if not store:
                    logger.error("No store found for auto-discovery")
                    return

                # Double-check for ID collision before create (paranoia against race conditions)
                Gateway.objects.filter(estation_id__iexact=clean_id).update(estation_id=None)
                Gateway.objects.create(store=store, **update_data)
            else:
                # If we have multiple conflicting records, resolve by clearing the ID on non-primary ones
                if conflicts.count() > 1:
                    Gateway.objects.filter(estation_id__iexact=clean_id).exclude(pk=gateway.pk).update(estation_id=None)

                # Update the primary record
                Gateway.objects.filter(pk=gateway.pk).update(**update_data)

            logger.info(f"Gateway {mac} (ID:{clean_id}) updated via /infor")
        except Exception:
            logger.exception(f"Error handling infor for gateway {estation_id}")

    def _process_tags(self, estation_id, tags_list):
        """
        TAG AUTO-DISCOVERY & TELEMETRY ENGINE
        -------------------------------------
        """
        try:
            if not tags_list: return

            gateway = Gateway.objects.filter(estation_id__iexact=estation_id.strip()).select_related('store').first()
            if not gateway: return

            normalized_macs = []
            tag_data_map = {}

            for tag_entry in tags_list:
                if isinstance(tag_entry, list):
                    raw_mac = tag_entry[0]
                    battery = tag_entry[2]
                else:
                    raw_mac = tag_entry.get('TagId')
                    battery = tag_entry.get('Battery')

                if raw_mac:
                    clean_mac = raw_mac.replace(':', '').upper()
                    normalized_macs.append(clean_mac)
                    tag_data_map[clean_mac] = {'battery': battery, 'original_mac': raw_mac}

            # Flexible database matching
            from django.db.models.functions import Upper, Replace
            from django.db.models import Value

            existing_tags_list = ESLTag.objects.filter(store=gateway.store).annotate(
                clean_db_mac=Upper(Replace('tag_mac', Value(':'), Value('')))
            ).filter(clean_db_mac__in=normalized_macs)

            existing_tags = {t.clean_db_mac: t for t in existing_tags_list}

            from .models import TagHardware
            default_hw = None
            tags_to_update = {}
            tags_to_create = {}
            now = timezone.now()
            default_hw_queried = False

            for clean_mac, metadata in tag_data_map.items():
                tag = existing_tags.get(clean_mac)
                battery_pct = self._calculate_battery_percentage(metadata['battery'])

                if tag:
                    tag.last_successful_gateway_id = estation_id
                    if battery_pct is not None:
                        tag.battery_level = battery_pct
                    tag.updated_at = now
                    if not tag.store: tag.store = gateway.store
                    tags_to_update[clean_mac] = tag
                else:
                    if default_hw is None and not default_hw_queried:
                        default_hw = TagHardware.objects.first()
                        default_hw_queried = True

                    tags_to_create[clean_mac] = ESLTag(
                        tag_mac=metadata['original_mac'],
                        battery_level=battery_pct if battery_pct is not None else 100,
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
        COMMAND: UPDATE TAG IMAGE (taskESL)
        ----------------------------------
        Sends a BMP image to a physical tag. Matches user sandbox script.
        """
        try:
            # AUTO-CONNECT: Ensure we are connected before publishing
            # (Crucial for web/Celery processes that don't run the worker loop)
            if not self.client.is_connected():
                logger.info("MQTT client not connected - attempting to connect before publish")
                self.connect()
                # Brief wait for connection to establish
                import time
                time.sleep(0.5)

            if not self.client.is_connected():
                logger.error("MQTT client not connected — cannot publish")
                return False

            import base64
            # 0. Clean Tag MAC (Remove colons and UPPERCASE to match hardware expectation)
            clean_mac = tag_mac.replace(':', '').upper()

            # 1. Base64 encode the BMP image
            image_b64 = base64.b64encode(image_bytes).decode('utf-8')

            # 2. taskESL Parameters: [TagId, Pattern, PageIndex, R, G, B, Times, Token, OldKey, NewKey, ImageB64]
            # Pattern=0, PageIndex=0, R=True, G=False, B=False, Times=0
            task_params = [clean_mac, 0, 0, True, False, False, 0, token, "", "", image_b64]

            # 3. Wrap in a list as expected by hardware: [[params]]
            # use_bin_type=True is required for the hardware to process the Base64 image payload correctly
            payload = msgpack.packb([task_params], use_bin_type=True)

            # 4. Use confirmed topic: /estation/{id}/taskESL (Ensuring uppercase ID)
            topic = f"/estation/{gateway_id.upper()}/taskESL"

            # Use QoS 0 for maximum compatibility
            result = self.client.publish(topic, payload, qos=0)

            # Log the full payload for debugging
            self._log_mqtt_message("sent", gateway_id, topic, task_params)

            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published taskESL update for {clean_mac} to gateway {gateway_id}. B64: {image_b64[:50]}...")
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

            payload = msgpack.packb(config_data, use_bin_type=True)
            topic = f"/estation/{gateway_id.upper()}/configure"

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
        """
        try:
            class BytesEncoder(json.JSONEncoder):
                def default(self, obj):
                    if isinstance(obj, bytes):
                        try:
                            return obj.decode('utf-8')
                        except:
                            return f"<binary:{len(obj)} bytes>"
                    return super().default(obj)

            json_data = json.dumps(data, cls=BytesEncoder)

            if force_success is not None:
                is_success = force_success
            else:
                is_success = True
                if direction == "received" and topic.endswith("/result"):
                    # Extract all status codes from the payload (supports single and multi-tag)
                    status_codes = []
                    if isinstance(data, list):
                        # Multi-tag format: [Port, Wait, Send, Msg, [Tags]]
                        if len(data) >= 5 and isinstance(data[4], list):
                            status_codes = [tr[4] for tr in data[4] if isinstance(tr, list) and len(tr) >= 5]
                        else:
                            # Single-tag format: [TagID, Rf, Batt, Ver, Status, ...]
                            d = data[0] if len(data) == 1 and isinstance(data[0], list) else data
                            if isinstance(d, list) and len(d) >= 5:
                                status_codes = [d[4]]
                    elif isinstance(data, dict):
                        status_codes = [data.get('Status')]

                    if status_codes:
                        # Message is successful ONLY if all tags succeeded (1 and 128 are SUCCESS)
                        is_success = all(s == 1 or s == 128 for s in status_codes)

            MQTTMessage.objects.create(
                direction=direction,
                estation_id=estation_id,
                topic=topic,
                data=json_data,
                is_success=is_success
            )

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
