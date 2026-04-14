
from django.test import TestCase
from core.models import Company, Store, Gateway, TagHardware, ESLTag, Product
from django.core.exceptions import ValidationError
from django.db import IntegrityError

class ESLTagUniquenessTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.store1 = Store.objects.create(name="Store 1", company=self.company, location_code="S1")
        self.store2 = Store.objects.create(name="Store 2", company=self.company, location_code="S2")

        self.gw1 = Gateway.objects.create(gateway_mac="GW1", store=self.store1, estation_id="GW1")
        self.gw2 = Gateway.objects.create(gateway_mac="GW2", store=self.store2, estation_id="GW2")

        self.hw = TagHardware.objects.create(
            model_number="Mi05", width_px=200, height_px=100, display_size_inch=2.1
        )

    def test_same_tag_different_stores(self):
        """Verify that the same tag_mac can exist in different stores."""
        tag1 = ESLTag.objects.create(tag_mac="390000F41F5F", gateway=self.gw1, hardware_spec=self.hw)
        tag2 = ESLTag.objects.create(tag_mac="390000F41F5F", gateway=self.gw2, hardware_spec=self.hw)

        self.assertEqual(tag1.store, self.store1)
        self.assertEqual(tag2.store, self.store2)
        self.assertEqual(ESLTag.objects.filter(tag_mac="390000F41F5F").count(), 2)

    def test_same_tag_same_store_fails(self):
        """Verify that the same tag_mac cannot exist twice in the same store."""
        ESLTag.objects.create(tag_mac="390000F41F5F", gateway=self.gw1, hardware_spec=self.hw)

        with self.assertRaises(ValidationError):
            ESLTag.objects.create(tag_mac="390000F41F5F", gateway=self.gw1, hardware_spec=self.hw)

    def test_store_auto_population(self):
        """Verify that the store is automatically populated from the gateway."""
        tag = ESLTag.objects.create(tag_mac="112233445566", gateway=self.gw1, hardware_spec=self.hw)
        self.assertEqual(tag.store, self.store1)

    def test_store_mismatch_validation(self):
        """Verify that cleaning fails if gateway and store are mismatched."""
        tag = ESLTag(tag_mac="998877665544", gateway=self.gw1, store=self.store2, hardware_spec=self.hw)
        with self.assertRaises(ValidationError):
            tag.full_clean()
