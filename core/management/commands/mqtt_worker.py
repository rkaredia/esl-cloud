from django.core.management.base import BaseCommand
from core.mqtt_client import mqtt_service
import time
import logging
from django.conf import settings

"""
MANAGEMENT COMMAND: MQTT WORKER
------------------------------
This is a standalone process that runs continuously in the background.
It connects to the MQTT broker and listens for messages from the
physical hardware (heartbeats, update results, etc.).

In production, this is usually managed by a process supervisor
like Systemd, Docker, or Supervisord.

USAGE: python manage.py mqtt_worker
"""

class Command(BaseCommand):
    help = 'Runs the MQTT listener for D21 eStation Gateways'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(
            f"Starting eStation MQTT Worker on {settings.MQTT_SERVER}:{settings.MQTT_PORT}..."
        ))
        
        try:
            # Connect to the broker (Configuration is pulled from settings.py)
            mqtt_service.connect()

            # KEEP-ALIVE LOOP
            # The MQTT client (Paho) runs in its own background thread.
            # We need to keep this main thread alive, otherwise the
            # entire script will exit immediately.
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            # Graceful shutdown when user presses Ctrl+C
            self.stdout.write(self.style.WARNING("Stopping MQTT Worker..."))
        except Exception as e:
            # Log any fatal crashes
            logging.getLogger(__name__).exception("MQTT Worker encountered a fatal error")
            self.stdout.write(self.style.ERROR(f"Fatal error: {e}"))
