from django.test import TestCase
from core.models import MQTTMessage
from core.mqtt_client import mqtt_service
import json

class MQTTSanitizationTest(TestCase):
    def test_mqtt_log_is_sanitized(self):
        """
        Verify that sensitive credentials are masked in MQTT logs.
        """
        sensitive_data = {
            'Alias': 'TestGW',
            'ConnParam': ['admin_user', 'secret_password_123'],
            'OtherData': 'Public'
        }

        mqtt_service._log_mqtt_message("sent", "GW01", "/estation/GW01/configure", sensitive_data)

        # Get the logged message
        log_entry = MQTTMessage.objects.latest('timestamp')
        log_data = json.loads(log_entry.data)

        # It should NOT contain the plain text password
        self.assertEqual(log_data['ConnParam'], '********')
        self.assertNotIn('secret_password_123', log_entry.data)
        self.assertNotIn('admin_user', log_entry.data)

    def test_mqtt_log_with_password_key_is_sanitized(self):
        """Verify direct 'password' keys are also sanitized."""
        sensitive_data = {
            'username': 'direct_user',
            'password': 'direct_password'
        }
        mqtt_service._log_mqtt_message("sent", "GW01", "/test/topic", sensitive_data)

        log_entry = MQTTMessage.objects.latest('timestamp')
        log_data = json.loads(log_entry.data)

        self.assertEqual(log_data['password'], '********')
        self.assertEqual(log_data['username'], '********')
        self.assertNotIn('direct_password', log_entry.data)
        self.assertNotIn('direct_user', log_entry.data)
