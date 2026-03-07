import paho.mqtt.client as mqtt
import msgpack
import json
import logging
from django.conf import settings
from django.utils import timezone
from .models import ESLTag, Gateway

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

            if msg.topic.endswith("/result"):
                self.handle_result(estation_id, data)
            elif msg.topic.endswith("/heartbeat"):
                self.handle_heartbeat(estation_id, data)
            elif msg.topic.endswith("/tagheartbeat"):
                self.handle_tag_heartbeat(estation_id, data)
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
            alias = data.get('Alias')
            ip = data.get('IP')
            mac = data.get('MAC')
            server = data.get('Server', '')
            conn_param = data.get('ConnParam', [])

            app_server_ip = None
            app_server_port = None
            if server and ':' in server:
                parts = server.split(':', 1)
                app_server_ip = parts[0]
                try:
                    app_server_port = int(parts[1])
                except ValueError:
                    pass
            elif server:
                app_server_ip = server

            username = conn_param[0] if len(conn_param) > 0 else None
            password = conn_param[1] if len(conn_param) > 1 else None

            Gateway.objects.filter(estation_id=gateway_id).update(
                alias=alias,
                gateway_ip=ip,
                gateway_mac=mac,
                app_server_ip=app_server_ip,
                app_server_port=app_server_port,
                username=username,
                password=password,
                last_heartbeat=timezone.now(),
                last_successful_heartbeat=timezone.now(),
                is_online=True,
                last_seen=timezone.now()
            )
        except Exception:
            logger.exception(f"Error handling heartbeat for gateway {estation_id}")

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
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published update for {tag_mac} to gateway {gateway_id}")
                return True
            else:
                logger.error(f"Failed to publish MQTT message for {tag_mac}: RC {result.rc}")
                return False
        except Exception:
            logger.exception(f"Exception during MQTT publish for tag {tag_mac}")
            return False

mqtt_service = ESLMqttClient()
