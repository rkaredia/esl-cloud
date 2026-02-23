import paho.mqtt.client as mqtt
import msgpack
import logging
import random
from django.conf import settings
from .models import ESLTag, Gateway

logger = logging.getLogger(__name__)

class ESLMqttClient:
    def __init__(self):
        self.client = mqtt.Client(protocol=mqtt.MQTTv5)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        
        # Setup TLS if in production
        # self.client.tls_set(...)

    def connect(self):
        try:
            self.client.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60)
            self.client.loop_start()
        except Exception as e:
            logger.error(f"MQTT Connection Failed: {e}")

    def on_connect(self, client, userdata, flags, rc, properties=None):
        logger.info(f"Connected to Broker with result code {rc}")
        # Subscribe to results for ALL estations (using wildcard for backend listener)
        self.client.subscribe("/estation/+/result", qos=2)
        self.client.subscribe("/estation/+/heartbeat", qos=0)

    def on_message(self, client, userdata, msg):
        topic_parts = msg.topic.split('/')
        estation_id = topic_parts[2]
        
        try:
            data = msgpack.unpackb(msg.payload)
            if "result" in msg.topic:
                self.handle_result(estation_id, data)
            elif "heartbeat" in msg.topic:
                self.handle_heartbeat(estation_id, data)
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def handle_result(self, estation_id, data):
        """
        Data structure from Manual: [TagID, RfPower, Battery, Version, Status, Token, Temp, Channel]
        """
        tag_id = data[0]
        battery_raw = data[2]
        status_code = data[4]
        token = data[5]

        try:
            tag = ESLTag.objects.get(tag_mac=tag_id)
            # Match Token to confirm the specific task
            if tag.last_image_task_token == token:
                if status_code == 0: # 0 usually means OK in these systems
                    tag.sync_state = 'SUCCESS'
                else:
                    tag.sync_state = 'FAILED'
                
                tag.battery_level = battery_raw # Map to % logic later
                tag.save()
                logger.info(f"Tag {tag_id} synced successfully with token {token}")
        except ESLTag.DoesNotExist:
            logger.warning(f"Result received for unknown tag: {tag_id}")

    def handle_heartbeat(self, estation_id, data):
        Gateway.objects.filter(estation_id=estation_id).update(
            last_seen=timezone.now(),
            is_online=True
        )

    def publish_tag_update(self, gateway_id, tag_mac, image_bytes, token):
        """
        Constructs the taskESL2 payload:
        [ [TagID, Pattern, PageIndex, R, G, B, Times, Token, OldKey, NewKey], [ImageBytes] ]
        """
        task_params = [
            tag_mac,    # TagID
            0,          # Pattern (0 for image)
            0,          # PageIndex
            True,       # R (Red channel)
            False,      # G
            False,      # B
            0,          # Times
            token,      # Token for tracking
            "",         # OldKey
            ""          # NewKey
        ]
        
        payload = msgpack.packb([task_params, image_bytes])
        topic = f"/estation/{gateway_id}/taskESL2"
        self.client.publish(topic, payload, qos=2)

mqtt_service = ESLMqttClient()