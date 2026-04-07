from django.test import TestCase, Client
from django.urls import reverse
from core.models import Company, Store, TagHardware, Gateway, ESLTag, User, Product
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from io import BytesIO
from openpyxl import Workbook
from core.admin.hardware import ESLTagAdmin
from core.admin.base import admin_site

class SentinelSecurityTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.store = Store.objects.create(name="Test Store", company=self.company)
        self.spec = TagHardware.objects.create(model_number="Mi05", width_px=296, height_px=128, display_size_inch=2.1)
        self.gateway = Gateway.objects.create(gateway_mac="GW001", store=self.store, estation_id="E01")

        # Create a 'Read Only' user
        self.readonly_user = User.objects.create_user(
            username='readonly_user',
            password='password123',
            email='readonly@example.com',
            company=self.company,
            role='readonly',
            is_staff=True
        )
        # Assign to 'Read Only' group
        readonly_group, _ = Group.objects.get_or_create(name='Read Only')

        # Give view permissions only
        for model in [ESLTag, Product, Gateway]:
            ct = ContentType.objects.get_for_model(model)
            view_perm = Permission.objects.get(content_type=ct, codename=f'view_{model._meta.model_name}')
            readonly_group.permissions.add(view_perm)

        self.readonly_user.groups.add(readonly_group)
        self.readonly_user.managed_stores.add(self.store)

        # Create a tag for UI testing
        self.tag = ESLTag.objects.create(tag_mac="TAG001", store=self.store, gateway=self.gateway, hardware_spec=self.spec)

    def create_excel_file(self):
        wb = Workbook()
        ws = wb.active
        ws.append(['tag_mac', 'gateway_mac', 'model_name'])
        ws.append(["NEWTAG001", "GW001", "Mi05"])
        f = BytesIO()
        wb.save(f)
        f.seek(0)
        f.name = 'test.xlsx'
        return f

    def test_readonly_user_cannot_import_tags(self):
        """
        Verify that a user with only 'view' permissions cannot use the tag import view to create tags.
        """
        self.client.login(username='readonly_user', password='password123')

        # Set active store
        session = self.client.session
        session['active_store_id'] = self.store.id
        session.save()

        excel_file = self.create_excel_file()

        # Try to import
        response = self.client.post(reverse('admin:preview_tag_import'), {'file': excel_file})

        # Check if tag was created
        tag_exists = ESLTag.objects.filter(tag_mac="NEWTAG001").exists()
        self.assertFalse(tag_exists, "Read-only user should not be able to create tags via import.")

    def test_sync_button_hidden_for_readonly_user(self):
        """
        Verify that the sync button is not rendered for a user without change_esltag permission.
        """
        self.client.login(username='readonly_user', password='password123')

        # We need a request object to test the admin method properly
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get(reverse('admin:core_esltag_changelist'))
        request.user = self.readonly_user

        model_admin = ESLTagAdmin(ESLTag, admin_site)
        # Trigger get_queryset to capture the request in our new mixin
        model_admin.get_queryset(request)

        html = model_admin.sync_button(self.tag)
        self.assertEqual(html, "", "Sync button should be empty for read-only user.")
