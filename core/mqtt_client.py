import paho.mqtt.client as mqtt
import msgpack
import json
import logging
import os
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from .models import ESLTag, Gateway, Store, GlobalSetting, MQTTMessage

logger = logging.getLogger(__name__)

class ESLMqttClient:
    """
    MQTT client for communicating with physical ESL gateways.
    Handles task publication and processing of result/heartbeat messages.
    """
    def __init__(self):
        try:
            self.client = mqtt.Client(protocol=mqtt.MQTTv5)
            self.client.on_connect = self.on_connect
            self.client.on_message = self.on_message
        except Exception:
            logger.exception("Failed to initialize MQTT client")

    def connect(self):
        """Initializes the connection to the MQTT broker."""
        try:
            host = getattr(settings, 'MQTT_SERVER', 'localhost')
            port = getattr(settings, 'MQTT_PORT', 1883)
            self.client.connect(host, port, 60)
            self.client.loop_start()
            logger.info(f"MQTT Client loop started for {host}:{port}")
        except Exception:
            logger.exception("MQTT Connection Failed")

    def on_connect(self, client, userdata, flags, rc, properties=None):
        """Callback for when the client connects to the broker."""
        try:
            logger.info(f"Connected to MQTT Broker with result code {rc}")
            self.client.subscribe("/estation/+/result", qos=2)
            self.client.subscribe("/estation/+/heartbeat", qos=0)
            self.client.subscribe("/estation/+/tagheartbeat", qos=0)
            self.client.subscribe("/estation/+/infor", qos=0)
            self.client.subscribe("/estation/+/message", qos=0)
        except Exception:
            logger.exception("Error in MQTT on_connect callback")

    def on_message(self, client, userdata, msg):
        """Central message handler for all subscribed topics."""
        try:
            topic_parts = msg.topic.split('/')
            if len(topic_parts) < 3: return
            estation_id = topic_parts[2]

            try:
                data = msgpack.unpackb(msg.payload)
            except Exception:
                try:
                    data = json.loads(msg.payload.decode())
                except Exception:
                    logger.error(f"Failed to unpack MQTT payload on {msg.topic}")
                    return

            self._log_mqtt_message("received", estation_id, msg.topic, data)

            if msg.topic.endswith("/result"):
                self.handle_result(estation_id, data)
            elif msg.topic.endswith("/heartbeat"):
                self.handle_heartbeat(estation_id, data)
            elif msg.topic.endswith("/tagheartbeat"):
                self.handle_tag_heartbeat(estation_id, data)
            elif msg.topic.endswith("/infor"):
                self.handle_infor(estation_id, data)
            elif msg.topic.endswith("/message"):
                # Logged via _log_mqtt_message already
                pass
        except Exception:
            logger.exception(f"Error processing MQTT message on topic {msg.topic}")

    def handle_result(self, estation_id, data):
        """Processes result data from a tag update task."""
        try:
            # Data might be msgpack (list) or JSON (dict)
            if isinstance(data, list):
                # Data: [TagID, RfPower, Battery, Version, Status, Token, Temp, Channel]
                if len(data) < 6: return
                tag_mac, battery_raw, status_code, token = data[0], data[2], data[4], data[5]
            else:
                # Assuming JSON dict
                tag_mac = data.get('TagId')
                battery_raw = data.get('Battery')
                status_code = data.get('Status')
                token = data.get('Token')

            tag = ESLTag.objects.filter(tag_mac=tag_mac).first()
            if tag and tag.last_image_task_token == token:
                is_success = (status_code == 0)
                tag.sync_state = 'SUCCESS' if is_success else 'FAILED'
                tag.battery_level = battery_raw
                if is_success:
                    tag.last_successful_gateway_id = estation_id
                tag.save(update_fields=['sync_state', 'battery_level', 'last_successful_gateway_id'])
                logger.info(f"Tag {tag_mac} sync result: {'SUCCESS' if is_success else 'FAILED'}")
        except Exception:
            logger.exception("Error handling MQTT result message")

    def handle_heartbeat(self, estation_id, data):
        """Updates the gateway status based on heartbeat messages."""
        try:
            # Expected JSON: {"ID":"0CGV","Alias":"01","IP":"...","MAC":"...","Server":"192.168.1.92:9081","ConnParam":["user","pass"],...}
            gateway_id = data.get('ID', estation_id)
            mac = data.get('MAC')

            update_data = {
                'alias': data.get('Alias'),
                'gateway_ip': data.get('IP'),
                'gateway_mac': mac,
                'ap_version': data.get('ApVersion'),
                'free_space': data.get('FreeSpace'),
                'heartbeat_interval': data.get('Heartbeat'),
                'last_heartbeat': timezone.now(),
                'last_successful_heartbeat': timezone.now(),
                'is_online': True,
                'last_seen': timezone.now()
            }

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

            conn_param = data.get('ConnParam', [])
            if len(conn_param) >= 2:
                update_data['username'] = conn_param[0]
                update_data['password'] = conn_param[1]

            Gateway.objects.filter(estation_id=gateway_id).update(**update_data)
        except Exception:
            logger.exception(f"Error handling heartbeat for gateway {estation_id}")

    def handle_infor(self, estation_id, data):
        """Processes device info and auto-registers gateways."""
        try:
            # Property: ID, Alias, IP, MAC, ApVersion, FreeSpace, Server, Heartbeat
            mac = data.get('MAC')
            if not mac: return

            admin_store = Store.objects.filter(name='Admin Store').first()
            if not admin_store:
                logger.error("Admin Store not found for auto-discovery")
                return

            gateway, created = Gateway.objects.get_or_create(
                gateway_mac=mac,
                defaults={
                    'estation_id': data.get('ID', estation_id),
                    'alias': data.get('Alias'),
                    'gateway_ip': data.get('IP'),
                    'ap_version': data.get('ApVersion'),
                    'free_space': data.get('FreeSpace'),
                    'heartbeat_interval': data.get('Heartbeat'),
                    'store': admin_store,
                    'is_online': True,
                    'last_heartbeat': timezone.now(),
                    'last_seen': timezone.now()
                }
            )

            if not created:
                # Update existing record
                gateway.estation_id = data.get('ID', gateway.estation_id)
                gateway.alias = data.get('Alias', gateway.alias)
                gateway.gateway_ip = data.get('IP', gateway.gateway_ip)
                gateway.ap_version = data.get('ApVersion', gateway.ap_version)
                gateway.free_space = data.get('FreeSpace', gateway.free_space)
                gateway.heartbeat_interval = data.get('Heartbeat', gateway.heartbeat_interval)
                gateway.is_online = True
                gateway.last_heartbeat = timezone.now()
                gateway.last_seen = timezone.now()
                gateway.save()

            logger.info(f"Gateway {mac} (ID:{estation_id}) {'registered' if created else 'updated'} via /infor")
        except Exception:
            logger.exception(f"Error handling infor for gateway {estation_id}")

    def handle_tag_heartbeat(self, estation_id, data):
        """Updates tag mapping and battery based on tag heartbeat messages."""
        try:
            # Expected JSON: {"Tags":[{"TagId":"...","Battery":30,...}],...}
            tags_list = data.get('Tags', [])
            for tag_data in tags_list:
                tag_mac = tag_data.get('TagId')
                battery = tag_data.get('Battery')

                if tag_mac:
                    ESLTag.objects.filter(tag_mac=tag_mac).update(
                        last_successful_gateway_id=estation_id,
                        battery_level=battery,
                        updated_at=timezone.now()
                    )
        except Exception:
            logger.exception(f"Error handling tag heartbeat for gateway {estation_id}")

    def publish_tag_update(self, gateway_id, tag_mac, image_bytes, token):
        """Publishes an image update task to a specific gateway."""
        try:
            task_params = [tag_mac, 0, 0, True, False, False, 0, token, "", ""]
            payload = msgpack.packb([task_params, image_bytes])
            topic = f"/estation/{gateway_id}/taskESL2"

            result = self.client.publish(topic, payload, qos=2)

            # Log sent message (hide binary for log)
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

    def publish_config(self, gateway_id, alias, server, encrypt, heartbeat, auto_ip=True, local_ip="", subnet="", gateway=""):
        """Publishes configuration to a specific gateway."""
        try:
            config_data = {
                'Alias': alias,
                'Server': server,
                'ConnParam': ["admin", "admin123"], # Defaults
                'Encrypt': encrypt,
                'AutoIP': auto_ip,
                'LocalIP': local_ip,
                'Subnet': subnet,
                'Gateway': gateway,
                'Heartbeat': heartbeat
            }

            # Fetch credentials from DB if possible
            gw_obj = Gateway.objects.filter(estation_id=gateway_id).first()
            if gw_obj and gw_obj.username and gw_obj.password:
                config_data['ConnParam'] = [gw_obj.username, gw_obj.password]

            payload = msgpack.packb(config_data)
            topic = f"/estation/{gateway_id}/configure"

            result = self.client.publish(topic, payload, qos=2)

            # Success is based on result.rc
            is_success = (result.rc == mqtt.MQTT_ERR_SUCCESS)

            # Log to DB and file
            self._log_mqtt_message("sent", gateway_id, topic, config_data, force_success=is_success)

            return is_success
        except Exception:
            logger.exception(f"Exception during MQTT publish config for gateway {gateway_id}")
            return False

    def _log_mqtt_message(self, direction, estation_id, topic, data, force_success=None):
        """Logs MQTT messages to daily files and Database."""
        try:
            # Use a custom JSON encoder to handle bytes
            class BytesEncoder(json.JSONEncoder):
                def default(self, obj):
                    if isinstance(obj, bytes):
                        return f"<binary:{len(obj)} bytes>"
                    return super().default(obj)

            json_data = json.dumps(data, cls=BytesEncoder)

            # 1. Log to Database
            if force_success is not None:
                is_success = force_success
            else:
                is_success = True
                if direction == "received" and topic.endswith("/result"):
                    # Simple success detection for results
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

            # 2. Log to File
            log_dir = os.path.join(settings.BASE_DIR, 'logs', 'mqtt', direction)
            os.makedirs(log_dir, exist_ok=True)

            filename = f"{datetime.now().strftime('%Y-%m-%d')}.log"
            filepath = os.path.join(log_dir, filename)

            with open(filepath, 'a') as f:
                timestamp = datetime.now().isoformat()
                f.write(f"[{timestamp}] ID:{estation_id} TOPIC:{topic} DATA:{json_data}\n")

        except Exception:
            logger.exception("Failed to log MQTT message")

mqtt_service = ESLMqttClient()
