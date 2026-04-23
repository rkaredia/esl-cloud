from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from core.models import Company, Store, Product
import openpyxl
from io import BytesIO

User = get_user_model()

class ProductImportSecurityTest(TestCase):
    """
    Security tests for the Product Import functionality.
    Ensures that granular RBAC (Add vs Change) is enforced during bulk imports.
    """
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.store = Store.objects.create(name="Test Store", company=self.company)

        # User with ONLY change_product permission
        self.staff_change_only = User.objects.create_user(
            username='staff_change_only',
            password='password123',
            company=self.company,
            role='staff',
            is_staff=True
        )
        self.staff_change_only.user_permissions.add(Permission.objects.get(codename='change_product'))
        self.staff_change_only.managed_stores.add(self.store)

        # User with ONLY add_product permission
        self.staff_add_only = User.objects.create_user(
            username='staff_add_only',
            password='password123',
            company=self.company,
            role='staff',
            is_staff=True
        )
        self.staff_add_only.user_permissions.add(Permission.objects.get(codename='add_product'))
        self.staff_add_only.managed_stores.add(self.store)

        # Create an existing product for update tests
        self.existing_product = Product.objects.create(
            sku="EXISTING123",
            name="Existing Product",
            price=10.00,
            store=self.store
        )

    def create_excel_file(self, rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Scan Code', 'Item Description', 'Unit Price'])
        for row in rows:
            ws.append(row)

        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = 'test_import.xlsx'
        return buf

    def test_import_add_requires_add_permission(self):
        """
        Verify that a user with only 'change' permission cannot add new products via import.
        """
        self.client.login(username='staff_change_only', password='password123')
        session = self.client.session
        session['active_store_id'] = self.store.id
        session.save()

        excel_data = [["NEW456", "New Product", "15.00"]]
        excel_file = self.create_excel_file(excel_data)

        # Step 1: Preview (Should show as rejected)
        response = self.client.post(reverse('admin:import-modisoft'), {'import_file': excel_file}, follow=True)
        self.assertEqual(len(response.context['results']['new']), 0)
        self.assertEqual(len(response.context['results']['rejected']), 1)
        self.assertIn("Permission Denied: Cannot add new products", response.context['results']['rejected'][0]['reason'])

        # Step 2: Confirm (Should not create anything)
        temp_filename = response.context['temp_filename']
        self.client.post(reverse('admin:import-modisoft'), {'confirm_save': '1', 'temp_filename': temp_filename}, follow=True)

        self.assertFalse(Product.objects.filter(sku="NEW456").exists())

    def test_import_change_requires_change_permission(self):
        """
        Verify that a user with only 'add' permission cannot update existing products via import.
        """
        self.client.login(username='staff_add_only', password='password123')
        session = self.client.session
        session['active_store_id'] = self.store.id
        session.save()

        excel_data = [["EXISTING123", "Modified Name", "20.00"]]
        excel_file = self.create_excel_file(excel_data)

        # Step 1: Preview (Should show as rejected)
        response = self.client.post(reverse('admin:import-modisoft'), {'import_file': excel_file}, follow=True)
        self.assertEqual(len(response.context['results']['update']), 0)
        self.assertEqual(len(response.context['results']['rejected']), 1)
        self.assertIn("Permission Denied: Cannot update existing products", response.context['results']['rejected'][0]['reason'])

        # Step 2: Confirm (Should not update anything)
        temp_filename = response.context['temp_filename']
        self.client.post(reverse('admin:import-modisoft'), {'confirm_save': '1', 'temp_filename': temp_filename}, follow=True)

        self.existing_product.refresh_from_db()
        self.assertEqual(self.existing_product.price, 10.00) # Unchanged
