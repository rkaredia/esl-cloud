from django.test import TestCase
from django.contrib.auth import get_user_model
from core.models import Store, Gateway, ESLTag, Company

User = get_user_model()
from core.mqtt_client import mqtt_service
import json
from django.utils import timezone
from datetime import timedelta
from django.contrib.admin.sites import AdminSite
from core.admin.hardware import GatewayAdmin, ESLTagAdmin

class LogicalBindingTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.store = Store.objects.create(name="Test Store", company=self.company)
        self.superuser = User.objects.create_superuser('admin', 'admin@test.com', 'pass')
        self.staff = User.objects.create_user('staff', 'staff@test.com', 'pass', is_staff=True)

    def test_gateway_metadata(self):
        gateway = Gateway.objects.create(
            store=self.store,
            estation_id="0CGV",
            name="Front Entrance",
            alias="01"
        )
        self.assertEqual(gateway.estation_id, "0CGV")
        self.assertFalse(gateway.is_online)

    def test_heartbeat_parsing(self):
        gateway = Gateway.objects.create(store=self.store, estation_id="0CGV")
        heartbeat_data = {
            "ID": "0CGV",
            "Alias": "01",
            "IP": "192.168.1.100",
            "MAC": "90:A9:F7:32:0B:45",
            "ApType": 6,
            "ApVersion": "1.0.28.0",
            "Server": "192.168.1.92:9081",
            "Heartbeat": 60
        }
        mqtt_service.handle_heartbeat("0CGV", heartbeat_data)

        gateway.refresh_from_db()
        self.assertEqual(gateway.gateway_ip, "192.168.1.100")
        self.assertEqual(gateway.gateway_mac, "90:A9:F7:32:0B:45")
        self.assertEqual(gateway.app_server_ip, "192.168.1.92")
        self.assertTrue(gateway.is_online)

    def test_tag_heartbeat_updates_binding(self):
        from core.models import TagHardware
        hw = TagHardware.objects.create(model_number="V2", width_px=200, height_px=100, display_size_inch=2.1)
        gateway = Gateway.objects.create(store=self.store, estation_id="0CGV")
        tag = ESLTag.objects.create(store=self.store, tag_mac="ABCDEF123456", hardware_spec=hw)

        # Simulate tag heartbeat via gateway 0CGV
        heartbeat_data = {"Tags": [{"TagId": "ABCDEF123456", "Battery": 85}]}
        # In current version, heartbeats are processed via _process_tags or handle_heartbeat
        # We use a mocked heartbeat on the topic to simulate real behavior if needed,
        # but for unit tests we can call _process_tags directly or handle_heartbeat
        mqtt_service._process_tags("0CGV", heartbeat_data["Tags"])

        tag.refresh_from_db()
        self.assertEqual(tag.last_successful_gateway_id, "0CGV")
        self.assertEqual(tag.battery_level, 85)

    def test_tag_auto_discovery(self):
        # Create a gateway but no tag
        from core.models import TagHardware
        TagHardware.objects.create(model_number="V2", width_px=200, height_px=100, display_size_inch=2.1)
        gateway = Gateway.objects.create(store=self.store, estation_id="0CGV")

        # Simulate heartbeat for unknown tag
        heartbeat_data = {"Tags": [{"TagId": "NEWTAG999", "Battery": 90}]}
        mqtt_service._process_tags("0CGV", heartbeat_data["Tags"])

        # Verify tag was created
        new_tag = ESLTag.objects.filter(tag_mac="NEWTAG999").first()
        self.assertIsNotNone(new_tag)
        self.assertEqual(new_tag.store, self.store)
        self.assertEqual(new_tag.last_successful_gateway_id, "0CGV")
        self.assertEqual(new_tag.battery_level, 90)

    def test_gateway_admin_permissions(self):
        gateway = Gateway.objects.create(store=self.store, estation_id="0CGV", username="admin", password="secret_password")
        model_admin = GatewayAdmin(Gateway, AdminSite())

        # Superuser sees credentials
        fields_admin = model_admin.get_fields(type('Request', (), {'user': self.superuser}))
        self.assertIn('username', fields_admin)
        self.assertIn('password', fields_admin)

        # Staff user does not see credentials
        fields_staff = model_admin.get_fields(type('Request', (), {'user': self.staff}))
        self.assertNotIn('username', fields_staff)
        self.assertNotIn('password', fields_staff)

    def test_esl_tag_readonly_fields(self):
        model_admin = ESLTagAdmin(ESLTag, AdminSite())
        readonly_fields = model_admin.get_readonly_fields(type('Request', (), {'user': self.superuser}))
        self.assertIn('gateway', readonly_fields)
        self.assertIn('last_successful_gateway_id', readonly_fields)
