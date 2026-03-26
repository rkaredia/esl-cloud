from django.test import TestCase, Client
from django.urls import reverse
from core.models import Company, Store, Gateway, MQTTMessage, User, ESLTag, TagHardware
from core.admin.monitoring import MQTTMessageAdmin
from core.admin.base import admin_site

class MQTTMessageSecurityTest(TestCase):
    def setUp(self):
        # Company A setup
        self.company_a = Company.objects.create(name="Company A")
        self.store_a = Store.objects.create(name="Store A", company=self.company_a)
        self.gw_a = Gateway.objects.create(estation_id="GW01", gateway_mac="MAC01", store=self.store_a)
        self.user_a = User.objects.create_user(
            username='owner_a',
            password='password123',
            email='owner_a@example.com',
            company=self.company_a,
            role='owner',
            is_staff=True
        )

        # Company B setup
        self.company_b = Company.objects.create(name="Company B")
        self.store_b = Store.objects.create(name="Store B", company=self.company_b)
        self.gw_b = Gateway.objects.create(estation_id="GW02", gateway_mac="MAC02", store=self.store_b)

        # Create MQTT Messages
        self.msg_a = MQTTMessage.objects.create(
            direction='sent',
            estation_id='GW01',
            topic='/estation/GW01/task',
            data='{"test": "a"}'
        )
        self.msg_b = MQTTMessage.objects.create(
            direction='sent',
            estation_id='GW02',
            topic='/estation/GW02/task',
            data='{"test": "b"}'
        )

        self.client = Client()

    def test_owner_can_ONLY_see_own_company_messages(self):
        """
        Verify that a company owner can ONLY see messages from their own company.
        """
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(MQTTMessage)
        perm = Permission.objects.get(codename='view_mqttmessage', content_type=ct)
        self.user_a.user_permissions.add(perm)

        self.client.login(username='owner_a', password='password123')

        url = reverse('sais_admin:core_mqttmessage_changelist')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        # Should see Company A message
        self.assertContains(response, 'GW01')
        # Should NOT see Company B message
        self.assertNotContains(response, 'GW02')

    def test_owner_CANNOT_clear_all_messages(self):
        """
        Verify that a company owner cannot use the 'clear_all_messages' action.
        """
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(MQTTMessage)
        perm = Permission.objects.get(codename='view_mqttmessage', content_type=ct)
        self.user_a.user_permissions.add(perm)
        perm_delete = Permission.objects.get(codename='delete_mqttmessage', content_type=ct)
        self.user_a.user_permissions.add(perm_delete)

        self.client.login(username='owner_a', password='password123')

        url = reverse('sais_admin:core_mqttmessage_changelist')
        post_data = {
            'action': 'clear_all_messages',
            'select_across': '0',
            'index': '0',
            '_selected_action': [self.msg_a.pk]
        }
        response = self.client.post(url, post_data, follow=True)

        # Should show error message
        self.assertContains(response, "Only superusers can clear communication logs.")
        # Verify messages are still there
        self.assertEqual(MQTTMessage.objects.count(), 2)

    def test_superuser_can_clear_all_messages(self):
        """
        Verify that a superuser can still clear messages.
        """
        superuser = User.objects.create_superuser(username='admin_user', password='password123', email='admin@example.com')
        self.client.login(username='admin_user', password='password123')

        # Set active store for superuser to avoid redirect
        session = self.client.session
        session['active_store_id'] = self.store_a.id
        session.save()

        url = reverse('sais_admin:core_mqttmessage_changelist')
        post_data = {
            'action': 'clear_all_messages',
            'select_across': '0',
            'index': '0',
            '_selected_action': [self.msg_a.pk]
        }
        response = self.client.post(url, post_data, follow=True)

        self.assertContains(response, "Cleared 2 messages.")
        self.assertEqual(MQTTMessage.objects.count(), 0)

class MQTTTagHeartbeatSecurityTest(TestCase):
    def setUp(self):
        # Store A setup
        self.company_a = Company.objects.create(name="Company A")
        self.store_a = Store.objects.create(name="Store A", company=self.company_a)
        self.gw_a = Gateway.objects.create(estation_id="GW01", gateway_mac="MAC01", store=self.store_a)

        # Store B setup
        self.company_b = Company.objects.create(name="Company B")
        self.store_b = Store.objects.create(name="Store B", company=self.company_b)
        self.gw_b = Gateway.objects.create(estation_id="GW02", gateway_mac="MAC02", store=self.store_b)

        # Common Tag MAC
        # Note: the system now normalizes for matching but uses original for creation
        self.shared_mac = "DEADBEEF0001"

        self.hw = TagHardware.objects.create(
            model_number="MODEL01",
            width_px=200,
            height_px=100,
            display_size_inch=2.1
        )

        # Register same MAC in both stores
        self.tag_a = ESLTag.objects.create(
            tag_mac=self.shared_mac,
            store=self.store_a,
            battery_level=100,
            sync_state='IDLE',
            hardware_spec=self.hw
        )
        self.tag_b = ESLTag.objects.create(
            tag_mac=self.shared_mac,
            store=self.store_b,
            battery_level=100,
            sync_state='IDLE',
            hardware_spec=self.hw
        )

        from core.mqtt_client import ESLMqttClient
        self.mqtt_service = ESLMqttClient()

    def test_heartbeat_from_store_a_does_not_affect_store_b(self):
        """
        SECURITY: Verify that a heartbeat from a gateway in Store A
        ONLY updates tags in Store A, even if the same MAC exists in Store B.
        """
        # Payload from Gateway A (Store A)
        # Note: 50 in our new conversion logic means 100% (since 30+ is 100%)
        # Let's use 26 which should result in 50% ((26-22)*12.5)
        data = {
            'Tags': [
                {'TagId': self.shared_mac, 'Battery': 26}
            ]
        }

        # Trigger heartbeat handler
        self.mqtt_service._process_tags("GW01", data['Tags'])

        # Refresh from DB
        self.tag_a.refresh_from_db()
        self.tag_b.refresh_from_db()

        # Store A tag SHOULD be updated
        self.assertEqual(self.tag_a.battery_level, 50)
        self.assertEqual(self.tag_a.last_successful_gateway_id, "GW01")

        # Store B tag SHOULD NOT be updated
        self.assertEqual(self.tag_b.battery_level, 100)
        self.assertNotEqual(self.tag_b.last_successful_gateway_id, "GW01")

class MQTTLoggingSecurityTest(TestCase):
    def setUp(self):
        from core.mqtt_client import ESLMqttClient
        self.mqtt_service = ESLMqttClient()

    def test_mqtt_log_masks_sensitive_data(self):
        """
        Verify that sensitive data like passwords and ConnParam are masked in MQTT logs.
        """
        sensitive_data = {
            'Alias': 'test',
            'Server': '127.0.0.1:9081',
            'ConnParam': ['user123', 'secret_password'],
            'password': 'another_secret',
            'SafeField': 'PublicValue'
        }

        # This should trigger _log_mqtt_message
        self.mqtt_service._log_mqtt_message("sent", "GW01", "/estation/GW01/configure", sensitive_data)

        # Get the log entry from DB
        log_entry = MQTTMessage.objects.first()
        import json
        logged_data = json.loads(log_entry.data)

        # Check if sensitive data is masked
        # Initially this will FAIL until I implement the masking
        self.assertEqual(logged_data['ConnParam'][1], '********', "ConnParam password not masked")
        self.assertEqual(logged_data['password'], '********', "'password' field not masked")
        self.assertEqual(logged_data['SafeField'], 'PublicValue', "Non-sensitive field was incorrectly masked")
