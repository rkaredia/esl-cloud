from django.test import TestCase, Client
from django.urls import reverse
from core.models import Company, Store, TagHardware, User

class DesignLabSecurityTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.store = Store.objects.create(name="Test Store", company=self.company)
        self.spec = TagHardware.objects.create(model_number="Mi05", width_px=296, height_px=128, display_size_inch=2.1)

        # Create a 'staff' user
        self.staff_user = User.objects.create_user(
            username='staff_user',
            password='password123',
            email='staff@example.com',
            company=self.company,
            role='staff',
            is_staff=True
        )
        self.staff_user.managed_stores.add(self.store)

        # Create a 'manager' user
        self.manager_user = User.objects.create_user(
            username='manager_user',
            password='password123',
            email='manager@example.com',
            company=self.company,
            role='manager',
            is_staff=True
        )
        self.manager_user.managed_stores.add(self.store)

    def test_staff_user_cannot_access_design_lab(self):
        """
        Verify that a staff user is denied access to the Design Lab.
        """
        self.client.login(username='staff_user', password='password123')

        # Access template gallery
        response = self.client.get(reverse('sais_admin:template-gallery'))
        self.assertEqual(response.status_code, 403, "Staff user should be denied access to template gallery.")

        # Access mock render
        response = self.client.get(reverse('sais_admin:template-render', args=[self.spec.id]))
        self.assertEqual(response.status_code, 403, "Staff user should be denied access to mock render.")

    def test_manager_user_can_access_design_lab(self):
        """
        Verify that a manager user can still access the Design Lab.
        """
        self.client.login(username='manager_user', password='password123')

        # Access template gallery
        response = self.client.get(reverse('sais_admin:template-gallery'))
        self.assertEqual(response.status_code, 200, "Manager user should be allowed access to template gallery.")

        # Access mock render
        response = self.client.get(reverse('sais_admin:template-render', args=[self.spec.id]))
        self.assertEqual(response.status_code, 200, "Manager user should be allowed access to mock render.")
