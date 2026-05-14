from django.test import TestCase, Client
from django.urls import reverse
from core.models import Company, User, TagHardware

class DesignLabSecurityTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.staff_user = User.objects.create_user(
            username='staff_user',
            password='password123',
            company=self.company,
            role='staff',
            is_staff=True
        )
        self.spec = TagHardware.objects.create(
            model_number="TEST_MODEL",
            width_px=200,
            height_px=100,
            display_size_inch=2.0
        )

    def test_staff_user_cannot_access_design_lab(self):
        """
        Verify that Staff users are denied access to the Design Lab.
        """
        self.client.login(username='staff_user', password='password123')

        # Test template gallery access
        gallery_url = reverse('sais_admin:template-gallery')
        response = self.client.get(gallery_url)
        self.assertEqual(response.status_code, 403)

        # Test mock render access
        render_url = reverse('sais_admin:template-render', args=[self.spec.id])
        response = self.client.get(render_url)
        self.assertEqual(response.status_code, 403)

    def test_owner_can_access_design_lab(self):
        """
        Verify that Owners still have access to the Design Lab.
        """
        owner_user = User.objects.create_user(
            username='owner_user',
            password='password123',
            company=self.company,
            role='owner',
            is_staff=True
        )
        self.client.login(username='owner_user', password='password123')

        gallery_url = reverse('sais_admin:template-gallery')
        response = self.client.get(gallery_url)
        self.assertEqual(response.status_code, 200)

    def test_manager_can_access_design_lab(self):
        """
        Verify that Managers still have access to the Design Lab.
        """
        manager_user = User.objects.create_user(
            username='manager_user',
            password='password123',
            company=self.company,
            role='manager',
            is_staff=True
        )
        self.client.login(username='manager_user', password='password123')

        gallery_url = reverse('sais_admin:template-gallery')
        response = self.client.get(gallery_url)
        self.assertEqual(response.status_code, 200)
