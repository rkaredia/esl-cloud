import json
from django.test import TestCase, RequestFactory
from django.utils import timezone
from core.models import Gateway, ESLTag, Store, Company, User, TagHardware
from core.mqtt_client import ESLMqttClient
from django.contrib.admin.sites import AdminSite
from core.admin.hardware import GatewayAdmin

class GatewayModelTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Co")
        self.store = Store.objects.create(name="Test Store", company=self.company)

    def test_gateway_creation(self):
        gateway = Gateway.objects.create(
            estation_id="0CGV",
            gateway_mac="90:A9:F7:32:0B:45",
            store=self.store,
            name="Front Entrance",
            alias="01"
        )
        self.assertEqual(gateway.estation_id, "0CGV")
        self.assertEqual(gateway.name, "Front Entrance")
        self.assertEqual(str(gateway), "0CGV - Front Entrance (Test Store)")

class MQTTClientTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Co")
        self.store = Store.objects.create(name="Test Store", company=self.company)
        self.hw = TagHardware.objects.create(model_number="H1", width_px=200, height_px=100, display_size_inch=2.1)
        self.gateway = Gateway.objects.create(
            estation_id="0CGV",
            gateway_mac="90:A9:F7:32:0B:45",
            store=self.store
        )
        self.tag = ESLTag.objects.create(
            tag_mac="8100005BD9A7",
            store=self.store,
            gateway=self.gateway,
            hardware_spec=self.hw
        )
        self.mqtt_client = ESLMqttClient()

    def test_handle_heartbeat(self):
        heartbeat_data = {
            "ID": "0CGV",
            "Alias": "01",
            "IP": "192.168.1.100",
            "MAC": "90:A9:F7:32:0B:45",
            "Server": "192.168.1.92:9081",
            "ConnParam": ["admin", "secret123"]
        }
        self.mqtt_client.handle_heartbeat("0CGV", heartbeat_data)

        self.gateway.refresh_from_db()
        self.assertTrue(self.gateway.is_online)
        self.assertEqual(self.gateway.alias, "01")
        self.assertEqual(self.gateway.gateway_ip, "192.168.1.100")
        self.assertEqual(self.gateway.app_server_ip, "192.168.1.92")
        self.assertEqual(self.gateway.app_server_port, 9081)
        self.assertEqual(self.gateway.username, "admin")
        self.assertEqual(self.gateway.password, "secret123")

    def test_handle_tag_heartbeat(self):
        tag_heartbeat_data = {
            "Tags": [{
                "TagId": "8100005BD9A7",
                "Battery": 45
            }]
        }
        self.mqtt_client.handle_tag_heartbeat("0CGV", tag_heartbeat_data)

        self.tag.refresh_from_db()
        self.assertEqual(self.tag.last_successful_gateway_id, "0CGV")
        self.assertEqual(self.tag.battery_level, 45)

class AdminPermissionsTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.company = Company.objects.create(name="Test Co")
        self.store = Store.objects.create(name="Test Store", company=self.company)
        self.gateway = Gateway.objects.create(
            estation_id="0CGV",
            gateway_mac="90:A9:F7:32:0B:45",
            store=self.store
        )
        self.superuser = User.objects.create_superuser(username='super', password='pass', email='s@test.com')
        self.staff_user = User.objects.create_user(username='staff', password='pass', is_staff=True)
        self.admin_site = AdminSite()
        self.gateway_admin = GatewayAdmin(Gateway, self.admin_site)

    def test_gateway_admin_permissions(self):
        # Superuser should have change permission
        request = self.factory.get('/')
        request.user = self.superuser
        self.assertTrue(self.gateway_admin.has_change_permission(request, self.gateway))

        # Non-superuser staff should NOT have change permission
        request.user = self.staff_user
        self.assertFalse(self.gateway_admin.has_change_permission(request, self.gateway))

    def test_gateway_admin_password_visibility(self):
        # Superuser should see password field
        request = self.factory.get('/')
        request.user = self.superuser
        fields = self.gateway_admin.get_fields(request, self.gateway)
        self.assertIn('password', fields)

        # Staff user should NOT see password field
        request.user = self.staff_user
        fields = self.gateway_admin.get_fields(request, self.gateway)
        self.assertNotIn('password', fields)
