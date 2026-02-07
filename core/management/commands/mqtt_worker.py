from django.core.management.base import BaseCommand
import paho.mqtt.client as mqtt
from django.conf import settings
from core.models import ESLTag
from django.utils import timezone
import json

class Command(BaseCommand):
    help = 'Runs the MQTT listener for Minew Gateways'

    def handle(self, *args, **options):
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        
        def on_message(client, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode())
                # Typical Minew Gateway battery status payload logic
                for device in payload.get('devices', []):
                    mac = device.get('mac')
                    battery = device.get('battery')
                    ESLTag.objects.filter(tag_mac=mac).update(
                        battery_level=battery,
                        last_seen=timezone.now()
                    )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error: {e}"))

        client.on_message = on_message
        client.connect(settings.MQTT_SERVER, settings.MQTT_PORT, 60)
        client.subscribe(settings.MQTT_TOPIC)
        self.stdout.write(self.style.SUCCESS('Starting MQTT Worker...'))
        client.loop_forever()