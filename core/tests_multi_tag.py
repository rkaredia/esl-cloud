from django.test import TestCase
from django.utils import timezone
from core.models import Gateway, Store, Company, ESLTag, TagHardware, MQTTMessage
from core.mqtt_client import mqtt_service
import json

class MultiTagResultTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.store = Store.objects.create(name="Admin Store", company=self.company)
        self.gateway = Gateway.objects.create(estation_id="GW01", store=self.store, gateway_mac="GW:MAC")
        self.hw = TagHardware.objects.create(model_number="ET0213", width_px=250, height_px=122, display_size_inch=2.13)

        # Create two tags
        self.tag1 = ESLTag.objects.create(
            tag_mac="840000C3281C",
            store=self.store,
            hardware_spec=self.hw,
            last_image_task_token=100
        )
        self.tag2 = ESLTag.objects.create(
            tag_mac="390000F41F5F",
            store=self.store,
            hardware_spec=self.hw,
            last_image_task_token=200
        )

    def test_handle_multi_tag_result_success(self):
        # Format: [Port, WaitCount, SendCount, MessageCode, [TagResult1, TagResult2]]
        # TagResult: [TagID, RfPower, Battery, Version, Status, Token, ...]
        data = [
            13, 0, 2, "",
            [
                ["840000C3281C", -69, 30, "v1", 1, 100, 27, 0], # Success (1)
                ["390000F41F5F", -256, 29, "v1", 128, 200, 0, 0] # Success (128)
            ]
        ]

        # Simulate log and processing
        mqtt_service._log_mqtt_message("received", "GW01", "/estation/GW01/result", data)
        mqtt_service.handle_result("GW01", data)

        self.tag1.refresh_from_db()
        self.tag2.refresh_from_db()

        self.assertEqual(self.tag1.sync_state, 'SUCCESS')
        self.assertEqual(self.tag1.battery_level, 100)
        self.assertEqual(self.tag2.sync_state, 'SUCCESS')
        self.assertEqual(self.tag2.battery_level, 87) # (29-22)*12.5 = 87.5 -> 87

        # Check MQTTMessage log
        log = MQTTMessage.objects.first()
        self.assertTrue(log.is_success)

    def test_handle_multi_tag_result_partial_failure(self):
        data = [
            13, 0, 2, "",
            [
                ["840000C3281C", -69, 30, "v1", 1, 100, 27, 0], # Success (1)
                ["390000F41F5F", -256, 29, "v1", 0, 200, 0, 0]  # Failure (0)
            ]
        ]

        mqtt_service._log_mqtt_message("received", "GW01", "/estation/GW01/result", data)
        mqtt_service.handle_result("GW01", data)

        self.tag1.refresh_from_db()
        self.tag2.refresh_from_db()

        self.assertEqual(self.tag1.sync_state, 'SUCCESS')
        self.assertEqual(self.tag2.sync_state, 'FAILED')

        # Check MQTTMessage log - Overall should be failure if any tag failed
        log = MQTTMessage.objects.first()
        self.assertFalse(log.is_success)

    def test_handle_multi_tag_result_all_failure(self):
        data = [
            13, 0, 2, "",
            [
                ["840000C3281C", -69, 30, "v1", 0, 100, 27, 0], # Failure (0)
                ["390000F41F5F", -256, 29, "v1", 2, 200, 0, 0]
            ]
        ]

        mqtt_service._log_mqtt_message("received", "GW01", "/estation/GW01/result", data)
        mqtt_service.handle_result("GW01", data)

        self.tag1.refresh_from_db()
        self.tag2.refresh_from_db()

        self.assertEqual(self.tag1.sync_state, 'FAILED')
        self.assertEqual(self.tag2.sync_state, 'FAILED')

        log = MQTTMessage.objects.first()
        self.assertFalse(log.is_success)

    def test_admin_ui_helpers(self):
        from core.admin.monitoring import MQTTMessageAdmin
        from core.admin.base import admin_site

        admin = MQTTMessageAdmin(MQTTMessage, admin_site)

        # Test success case
        msg_success = MQTTMessage.objects.create(
            direction='received',
            estation_id="GW01",
            topic="/estation/GW01/result",
            data=json.dumps([13, 0, 2, "", [
                ["840000C3281C", -69, 30, "v1", 1, 100],
                ["390000F41F5F", -256, 29, "v1", 128, 200]
            ]]),
            is_success=True
        )

        status_html = admin.status_indicator(msg_success)
        self.assertIn("SUCCESS", status_html)
        self.assertIn("#059669", status_html)

        tag_id_html = admin.tag_id_column(msg_success)
        self.assertIn("840000C3281C-Success", tag_id_html)
        self.assertIn("390000F41F5F-Success", tag_id_html)

        # Test partial failure
        msg_partial = MQTTMessage.objects.create(
            direction='received',
            estation_id="GW01",
            topic="/estation/GW01/result",
            data=json.dumps([13, 0, 2, "", [
                ["840000C3281C", -69, 30, "v1", 1, 100],
                ["390000F41F5F", -256, 29, "v1", 0, 200]
            ]]),
            is_success=False
        )

        status_html = admin.status_indicator(msg_partial)
        self.assertIn("PARTIAL FAILURE", status_html)
        self.assertIn("#f59e0b", status_html)

        tag_id_html = admin.tag_id_column(msg_partial)
        self.assertIn("840000C3281C-Success", tag_id_html)
        self.assertIn("390000F41F5F-Failure", tag_id_html)

        # Test total failure
        msg_failure = MQTTMessage.objects.create(
            direction='received',
            estation_id="GW01",
            topic="/estation/GW01/result",
            data=json.dumps([13, 0, 2, "", [
                ["840000C3281C", -69, 30, "v1", 0, 100],
                ["390000F41F5F", -256, 29, "v1", 2, 200]
            ]]),
            is_success=False
        )

        status_html = admin.status_indicator(msg_failure)
        self.assertIn("FAILURE", status_html)
        self.assertIn("#dc2626", status_html)
        self.assertNotIn("PARTIAL", status_html)
