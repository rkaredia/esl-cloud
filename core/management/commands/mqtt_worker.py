from django.core.management.base import BaseCommand
from core.mqtt_client import mqtt_service
import time
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Runs the MQTT listener for D21 eStation Gateways'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"Starting eStation MQTT Worker on {settings.MQTT_SERVER}:{settings.MQTT_PORT}..."))
        
        try:
            mqtt_service.connect()

            # Keep the main thread alive while the MQTT loop runs in the background
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Stopping MQTT Worker..."))
        except Exception as e:
            logger.exception("MQTT Worker encountered a fatal error")
            self.stdout.write(self.style.ERROR(f"Fatal error: {e}"))
