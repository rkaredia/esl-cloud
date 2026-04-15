from django.test import TestCase, Client
from django.urls import reverse
from io import BytesIO
from openpyxl import Workbook
from core.models import Company, Store, TagHardware, Gateway, ESLTag, User
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

class SafeDeleteSecurityTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.store = Store.objects.create(name="Test Store", company=self.company)
        self.spec = TagHardware.objects.create(model_number="Mi05", width_px=296, height_px=128, display_size_inch=2.1)
        self.gateway = Gateway.objects.create(gateway_mac="GW001", store=self.store, estation_id="E01")

        # Create a 'Read Only' user
        self.readonly_user = User.objects.create_user(
            username='readonly_user_delete',
            password='password123',
            email='readonly_delete@example.com',
            company=self.company,
            role='readonly',
            is_staff=True
        )
        # Assign to 'Read Only' group
        readonly_group, _ = Group.objects.get_or_create(name='Read Only')

        # Give view permissions only
        for model in [ESLTag, Store, Company]:
            ct = ContentType.objects.get_for_model(model)
            view_perm = Permission.objects.get(content_type=ct, codename=f'view_{model._meta.model_name}')
            readonly_group.permissions.add(view_perm)

        self.readonly_user.groups.add(readonly_group)
        self.readonly_user.managed_stores.add(self.store)

        # Create tags to be deleted - MACs must be 8-15 chars for sanitization to pass in views
        self.tag1 = ESLTag.objects.create(tag_mac="TAG00001", store=self.store, gateway=self.gateway, hardware_spec=self.spec)
        self.tag2 = ESLTag.objects.create(tag_mac="TAG00002", store=self.store, gateway=self.gateway, hardware_spec=self.spec)

    def test_readonly_user_cannot_safe_delete_tags(self):
        """
        Verify that a user without delete_esltag permission cannot use the safe_delete action.
        """
        self.client.login(username='readonly_user_delete', password='password123')

        # Set active store
        session = self.client.session
        session['active_store_id'] = self.store.id
        session.save()

        # Try to execute safe_delete action
        url = reverse('admin:core_esltag_changelist')
        data = {
            'action': 'safe_delete',
            '_selected_action': [self.tag1.pk, self.tag2.pk]
        }
        response = self.client.post(url, data, follow=True)

        # Check if tags still exist
        self.assertTrue(ESLTag.objects.filter(pk=self.tag1.pk).exists(), "Tag 1 should NOT have been deleted by read-only user.")
        self.assertTrue(ESLTag.objects.filter(pk=self.tag2.pk).exists(), "Tag 2 should NOT have been deleted by read-only user.")

    def create_excel_file(self, data):
        wb = Workbook()
        ws = wb.active
        ws.append(['tag_mac', 'gateway_mac', 'model_name'])
        for row in data:
            ws.append(row)
        f = BytesIO()
        wb.save(f)
        f.seek(0)
        f.name = 'test.xlsx'
        return f

    def test_change_only_user_cannot_add_tags_via_import(self):
        """
        Verify that a user with change permission but NO add permission cannot create new tags via import.
        """
        # Create a user with ONLY change permission
        change_user = User.objects.create_user(
            username='change_only_user',
            password='password123',
            email='change@example.com',
            company=self.company,
            role='staff',
            is_staff=True
        )
        ct = ContentType.objects.get_for_model(ESLTag)
        change_perm = Permission.objects.get(content_type=ct, codename='change_esltag')
        change_user.user_permissions.add(change_perm)
        change_user.managed_stores.add(self.store)

        self.client.login(username='change_only_user', password='password123')

        # Set active store
        session = self.client.session
        session['active_store_id'] = self.store.id
        session.save()

        # Excel with ONE existing tag (to update) and ONE new tag (to add)
        # Update tag1's gateway to GW001 (same as now) but we'll check it doesn't fail
        # Actually tag1 in setUp is already linked to GW001. Let's try to update its hardware spec or just gateway MAC.
        # But we need another gateway to see a change.
        gw2 = Gateway.objects.create(gateway_mac="GW002", store=self.store, estation_id="E02")

        excel_data = [
            ["TAG00001", "GW002", "Mi05"], # Update existing
            ["NEWTAG999", "GW001", "Mi05"] # Add new
        ]
        excel_file = self.create_excel_file(excel_data)

        response = self.client.post(reverse('admin:preview_tag_import'), {'file': excel_file})
        self.assertEqual(response.status_code, 200)

        # Verify update worked
        self.tag1.refresh_from_db()
        self.assertEqual(self.tag1.gateway, gw2, "Tag 1 should have been updated by user with change permission.")

        # Verify addition FAILED
        new_tag_exists = ESLTag.objects.filter(tag_mac="NEWTAG999").exists()
        self.assertFalse(new_tag_exists, "New tag should NOT have been created by user without add permission.")

        results = response.context['results']
        # results[0] is TAG001 (updated)
        # results[1] is NEWTAG999 (rejected)
        self.assertEqual(results[1]['status'], 'rejected')
        self.assertIn('Permission denied', results[1]['message'])
