from django.test import TestCase, Client
from django.urls import reverse
from core.models import Company, Store, TagHardware, Gateway, ESLTag, User, Product
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

class SentinelRBACGapTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.store1 = Store.objects.create(name="Store 1", company=self.company)
        self.store2 = Store.objects.create(name="Store 2", company=self.company)

        self.spec = TagHardware.objects.create(model_number="Mi05", width_px=296, height_px=128, display_size_inch=2.1)

        self.gateway1 = Gateway.objects.create(gateway_mac="GW1", store=self.store1, estation_id="E01")
        self.gateway2 = Gateway.objects.create(gateway_mac="GW2", store=self.store2, estation_id="E02")

        # Create a 'staff' user assigned ONLY to Store 1
        self.staff_user = User.objects.create_user(
            username='staff_user',
            password='password123',
            email='staff@example.com',
            company=self.company,
            role='staff',
            is_staff=True
        )
        self.staff_user.managed_stores.add(self.store1)

        # Give staff permissions
        staff_group, _ = Group.objects.get_or_create(name='Store Staff')
        for model in [ESLTag, Product, Gateway, Store]:
            ct = ContentType.objects.get_for_model(model)
            view_perm = Permission.objects.get(content_type=ct, codename=f'view_{model._meta.model_name}')
            staff_group.permissions.add(view_perm)
        self.staff_user.groups.add(staff_group)

        # Create data in both stores
        self.tag1 = ESLTag.objects.create(tag_mac="TAG1", store=self.store1, gateway=self.gateway1, hardware_spec=self.spec)
        self.tag2 = ESLTag.objects.create(tag_mac="TAG2", store=self.store2, gateway=self.gateway2, hardware_spec=self.spec)

        self.prod1 = Product.objects.create(sku="PROD1", name="Product 1", price=10.0, store=self.store1)
        self.prod2 = Product.objects.create(sku="PROD2", name="Product 2", price=20.0, store=self.store2)

    def test_staff_user_store_isolation_gap(self):
        """
        Verify that a 'staff' user is incorrectly able to see data from stores they are not assigned to.
        Currently, CompanySecurityMixin only isolates by store for 'manager' role.
        """
        self.client.login(username='staff_user', password='password123')

        # Test ESLTag visibility in Admin
        response = self.client.get(reverse('admin:core_esltag_changelist'))
        self.assertEqual(response.status_code, 200)

        # If the gap exists, they see both tags
        # If fixed, they should only see TAG1
        content = response.content.decode()
        self.assertIn("TAG1", content)

        # Verify isolation: TAG2 should NOT be visible to staff user assigned only to Store 1
        self.assertNotIn("TAG2", content, "VULNERABILITY: Staff user can see tags from unassigned stores!")

    def test_readonly_user_store_isolation_gap(self):
        """
        Verify that a 'readonly' user is also incorrectly able to see data from stores they are not assigned to.
        """
        readonly_user = User.objects.create_user(
            username='readonly_user',
            password='password123',
            email='readonly@example.com',
            company=self.company,
            role='readonly',
            is_staff=True
        )
        readonly_user.managed_stores.add(self.store1)

        # Give view permissions only
        readonly_group, _ = Group.objects.get_or_create(name='Read Only')
        for model in [ESLTag, Product, Gateway, Store]:
            ct = ContentType.objects.get_for_model(model)
            view_perm = Permission.objects.get(content_type=ct, codename=f'view_{model._meta.model_name}')
            readonly_group.permissions.add(view_perm)
        readonly_user.groups.add(readonly_group)

        self.client.login(username='readonly_user', password='password123')
        response = self.client.get(reverse('admin:core_esltag_changelist'))
        content = response.content.decode()

        self.assertIn("TAG1", content)
        self.assertNotIn("TAG2", content, "VULNERABILITY: Readonly user can see tags from unassigned stores!")
