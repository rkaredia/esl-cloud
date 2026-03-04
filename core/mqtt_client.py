import paho.mqtt.client as mqtt
import msgpack
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
        except Exception:
            logger.exception("Error in MQTT on_connect callback")

    def on_message(self, client, userdata, msg):
        """Central message handler for all subscribed topics."""
        try:
            topic_parts = msg.topic.split('/')
            if len(topic_parts) < 3: return
            estation_id = topic_parts[2]
            data = msgpack.unpackb(msg.payload)

            if "result" in msg.topic:
                self.handle_result(estation_id, data)
            elif "heartbeat" in msg.topic:
                self.handle_heartbeat(estation_id, data)
        except Exception:
            logger.exception(f"Error processing MQTT message on topic {msg.topic}")

    def handle_result(self, estation_id, data):
        """Processes result data from a tag update task."""
        try:
            # Data: [TagID, RfPower, Battery, Version, Status, Token, Temp, Channel]
            if len(data) < 6: return
            tag_id, battery_raw, status_code, token = data[0], data[2], data[4], data[5]
            tag = ESLTag.objects.filter(tag_mac=tag_id).first()
            if tag and tag.last_image_task_token == token:
                tag.sync_state = 'SUCCESS' if status_code == 0 else 'FAILED'
                tag.battery_level = battery_raw
                tag.save(update_fields=['sync_state', 'battery_level'])
                logger.info(f"Tag {tag_id} sync result: {'SUCCESS' if status_code == 0 else 'FAILED'}")
        except Exception:
            logger.exception("Error handling MQTT result message")

    def handle_heartbeat(self, estation_id, data):
        """Updates the gateway status based on heartbeat messages."""
        try:
            Gateway.objects.filter(estation_id=estation_id).update(
                last_seen=timezone.now(),
                is_online=True
            )
        except Exception:
            logger.exception(f"Error handling heartbeat for gateway {estation_id}")

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
