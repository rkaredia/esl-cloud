from django.test import TestCase
from django.utils import timezone
from core.models import Gateway, Store, Company
from core.mqtt_client import mqtt_service

class GatewayMqttTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.store = Store.objects.create(name="Admin Store", company=self.company)

    def test_handle_infor_auto_registration(self):
        # Format: [ID, Nickname, LocalIP, MAC, ApType, MainVer, ModVer, Disk, Available, ServerIP, ConnParam, AutoIP, FixedIP, Mask, Gateway, ??, Heartbeat]
        infor_data = [
            "0CGV", "00", "192.168.1.3", "90:A9:F7:32:0B:45", 6, "1.0.28.0",
            "1.0.088_1.0.088_1.0.088_1.0.088_", 1984, 1773, "192.168.1.92:9081",
            ["test", "123456"], True, True, "", "", "", 15
        ]

        mqtt_service.handle_infor("0CGV", infor_data)

        gateway = Gateway.objects.get(estation_id="0CGV")
        self.assertEqual(gateway.gateway_mac, "90:A9:F7:32:0B:45")
        self.assertEqual(gateway.gateway_ip, "192.168.1.3")
        self.assertEqual(gateway.ap_version, "1.0.28.0")
        self.assertEqual(gateway.module_version, "1.0.088_1.0.088_1.0.088_1.0.088_")
        self.assertEqual(gateway.disk_size, 1984)
        self.assertEqual(gateway.free_space, 1773)
        self.assertEqual(gateway.app_server_ip, "192.168.1.92")
        self.assertEqual(gateway.app_server_port, 9081)
        self.assertEqual(gateway.username, "test")
        self.assertEqual(gateway.password, "123456")
        self.assertEqual(gateway.heartbeat_interval, 15)
        self.assertTrue(gateway.is_online)
        self.assertEqual(gateway.store, self.store)

    def test_handle_heartbeat_9_element(self):
        Gateway.objects.create(estation_id="0CGV", store=self.store, gateway_mac="90:A9:F7:32:0B:45")

        # Format: [AP ID, ConfigVer, BaseVer, BlueVer, MsgCode, MsgExt, Queued, Comm, Tags]
        heartbeat_data = [
            "0CGV", 0, "1.0.28.0", "BT_VER", 4, "", 10, 5, []
        ]

        mqtt_service.handle_heartbeat("0CGV", heartbeat_data)

        gateway = Gateway.objects.get(estation_id="0CGV")
        self.assertEqual(gateway.ap_version, "1.0.28.0")
        self.assertEqual(gateway.module_version, "BT_VER")
        self.assertEqual(gateway.tags_queued_count, 10)
        self.assertEqual(gateway.tags_comm_count, 5)
        self.assertIsNone(gateway.last_error_message)
        self.assertTrue(gateway.is_online)

    def test_handle_heartbeat_error_code(self):
        Gateway.objects.create(estation_id="0CGV", store=self.store, gateway_mac="90:A9:F7:32:0B:45")

        heartbeat_data = [
            "0CGV", 0, "1.0.28.0", "BT_VER", 5, "", 0, 0, []
        ]

        mqtt_service.handle_heartbeat("0CGV", heartbeat_data)

        gateway = Gateway.objects.get(estation_id="0CGV")
        self.assertEqual(gateway.last_error_message, "ModError: Abnormality of the communication module")
        self.assertTrue(gateway.is_online) # Still online since it's communicating

    def test_gateway_offline_timeout(self):
        from core.tasks import check_gateways_status_task
        from django.utils import timezone
        import datetime

        # Create a gateway with 30s interval
        gw = Gateway.objects.create(
            estation_id="OFFLINE_TEST",
            store=self.store,
            gateway_mac="ABC",
            is_online=True,
            heartbeat_interval=30,
            last_heartbeat=timezone.now() - datetime.timedelta(seconds=121) # 4x 30s = 120s
        )

        # Run the background task
        check_gateways_status_task()

        gw.refresh_from_db()
        self.assertFalse(gw.is_online)
        self.assertIn("Offline: No heartbeat received", gw.last_error_message)
