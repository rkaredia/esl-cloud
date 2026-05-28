from django.test import TestCase, Client
from django.urls import reverse
from core.models import Company, Store, TagHardware, Gateway, ESLTag, User
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from core.admin.base import admin_site
from core.admin.hardware import ESLTagAdmin
from core.admin.organisation import CustomUserAdmin
from django.core.exceptions import PermissionDenied

class SentinelRBACReproductionTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.store = Store.objects.create(name="Test Store", company=self.company)
        self.spec = TagHardware.objects.create(model_number="Mi05", width_px=296, height_px=128, display_size_inch=2.1)

        # Manager User
        self.manager = User.objects.create_user(
            username='manager',
            password='password123',
            email='manager@example.com',
            company=self.company,
            role='manager',
            is_staff=True
        )
        self.manager.managed_stores.add(self.store)

        # Staff User
        self.staff = User.objects.create_user(
            username='staff_user',
            password='password123',
            email='staff@example.com',
            company=self.company,
            role='staff',
            is_staff=True
        )
        self.staff.managed_stores.add(self.store)

        # Tag WITH gateway
        self.gateway = Gateway.objects.create(gateway_mac="GW001", store=self.store, estation_id="E01")
        self.tag_with_gw = ESLTag.objects.create(tag_mac="TAG001", store=self.store, gateway=self.gateway, hardware_spec=self.spec)

        # Tag WITHOUT gateway
        self.tag_no_gw = ESLTag.objects.create(tag_mac="TAG002", store=self.store, gateway=None, hardware_spec=self.spec)

    def test_manager_cannot_see_tag_without_gateway(self):
        """
        Reproduction: Managers currently cannot see tags without gateways due to filtering in CompanySecurityMixin.
        """
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get('/')
        request.user = self.manager
        request.active_store = self.store

        model_admin = ESLTagAdmin(ESLTag, admin_site)
        qs = model_admin.get_queryset(request)

        self.assertIn(self.tag_with_gw, qs)
        # This is expected to FAIL before the fix
        self.assertIn(self.tag_no_gw, qs, "Manager should be able to see tags even if they don't have a gateway yet.")

    def test_manager_cannot_see_staff_users(self):
        """
        Reproduction: Managers currently cannot see staff users in their store.
        """
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get('/')
        request.user = self.manager
        request.active_store = self.store

        model_admin = CustomUserAdmin(User, admin_site)
        qs = model_admin.get_queryset(request)

        self.assertIn(self.manager, qs)
        # This is expected to FAIL before the fix
        self.assertIn(self.staff, qs, "Manager should be able to see staff users in their company/store.")

    def test_staff_cannot_access_design_lab(self):
        """
        Verify: Staff users cannot access the design lab.
        """
        self.client.login(username='staff_user', password='password123')

        # Access Template Lab
        response = self.client.get(reverse('sais_admin:template-gallery'))
        # admin_view raises PermissionDenied which Django's test client converts to 403
        self.assertEqual(response.status_code, 403, "Staff should not have access to template gallery")

        # Access Mock Render
        response = self.client.get(reverse('sais_admin:template-render', args=[self.spec.id]))
        self.assertEqual(response.status_code, 403, "Staff should not have access to mock render")
