import json
import paho.mqtt.client as mqtt
from django.conf import settings

def on_connect(client, userdata, flags, rc):
    # Subscribe to Minew gateway topics
    client.subscribe("/gw/+/status") 
    print("Connected to Minew MQTT Broker")

def on_message(client, userdata, msg):
    from core.models import ESLTag # Import inside to avoid circular issues
    try:
        data = json.loads(msg.payload)
        # Minew typically sends an array of tags
        for tag_data in data.get('tags', []):
            mac = tag_data.get('mac')
            batt = tag_data.get('battery')
            
            # Update the database
            ESLTag.objects.filter(tag_mac=mac).update(
                battery_level=batt,
                last_seen=timezone.now()
            )
    except Exception as e:
        print(f"MQTT Error: {e}")

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message