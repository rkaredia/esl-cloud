from io import BytesIO
from openpyxl import Workbook
from django.test import TestCase, Client
from django.urls import reverse
from .models import Company, Store, Gateway, TagHardware, ESLTag, User
from unittest.mock import patch

class SecurityTest(TestCase):
    def setUp(self):
        # Setup Companies and Stores
        self.company_a = Company.objects.create(name="Company A")
        self.store_a = Store.objects.create(name="Store A", company=self.company_a)

        self.company_b = Company.objects.create(name="Company B")
        self.store_b = Store.objects.create(name="Store B", company=self.company_b)

        # Setup Hardware Specs
        self.spec = TagHardware.objects.create(model_number="Mi05", width_px=296, height_px=128, display_size_inch=2.1)

        # Setup Gateways
        self.gw_a = Gateway.objects.create(gateway_mac="GW_A", store=self.store_a, estation_id="E01")
        self.gw_b = Gateway.objects.create(gateway_mac="GW_B", store=self.store_b, estation_id="E02")

        # Create a tag in Store A
        self.tag = ESLTag.objects.create(tag_mac="TAGABC001", gateway=self.gw_a, hardware_spec=self.spec)

        # Setup User (Admin can access everything, but we'll test store context isolation)
        self.user = User.objects.create_superuser(username='admin', password='password123', email='admin@example.com')
        self.client.login(username='admin', password='password123')

    def create_excel_file(self, data):
        wb = Workbook()
        ws = wb.active
        ws.append(['tag_mac', 'gateway_mac', 'model_name'])
        for row in data:
            ws.append(row)

        f = BytesIO()
        wb.save(f)
        f.seek(0)
        # Give it a name so Django's request.FILES works correctly
        f.name = 'test_import.xlsx'
        return f

    def test_cross_store_tag_existence(self):
        """
        Tests that a user in Store B can add a tag with the same ID as a tag in Store A,
        and both can exist independently.
        """
        # Manually set active store to Store B in the session
        session = self.client.session
        session['active_store_id'] = self.store_b.id
        session.save()

        # Prepare import file that adds TAGABC001 (already in Store A) to Store B
        excel_data = [["TAGABC001", "GW_B", "Mi05"]]
        excel_file = self.create_excel_file(excel_data)

        # Perform the import request
        response = self.client.post(reverse('admin:preview_tag_import'), {'file': excel_file})

        self.assertEqual(response.status_code, 200)

        # Verify the tag in Store A still belongs to Store A / GW_A
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.gateway, self.gw_a)

        # Verify a new tag record was created for Store B
        tag_b = ESLTag.objects.get(tag_mac="TAGABC001", store=self.store_b)
        self.assertEqual(tag_b.gateway, self.gw_b)

        # Verify it was added in the summary
        results = response.context['results']
        self.assertEqual(results[0]['status'], 'added')
