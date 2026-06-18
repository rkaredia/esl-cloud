from django.test import TestCase, Client
from django.urls import reverse
from core.models import Company, Store, TagHardware, Gateway, ESLTag, User, Product
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

class RBACGapTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Test Company")
        self.store_a = Store.objects.create(name="Store A", company=self.company)
        self.store_b = Store.objects.create(name="Store B", company=self.company)

        self.spec = TagHardware.objects.create(model_number="Mi05", width_px=296, height_px=128, display_size_inch=2.1)

        # Create a 'Staff' user assigned only to Store A
        self.staff_user = User.objects.create_user(
            username='staff_user',
            password='password123',
            email='staff@example.com',
            company=self.company,
            role='staff',
            is_staff=True
        )
        self.staff_user.managed_stores.add(self.store_a)

        # Give them general change permissions so they can see things in admin
        staff_group, _ = Group.objects.get_or_create(name='Store Staff')
        from core.models import MQTTMessage
        for model in [ESLTag, Product, Gateway, MQTTMessage]:
            ct = ContentType.objects.get_for_model(model)
            for perm_type in ['view', 'change']:
                perm = Permission.objects.get(content_type=ct, codename=f'{perm_type}_{model._meta.model_name}')
                staff_group.permissions.add(perm)
        self.staff_user.groups.add(staff_group)

        # Create tags in both stores
        self.tag_a = ESLTag.objects.create(tag_mac="TAGAAA", store=self.store_a, hardware_spec=self.spec)
        self.tag_b = ESLTag.objects.create(tag_mac="TAGBBB", store=self.store_b, hardware_spec=self.spec)

        # Create MQTT messages for both stores' gateways
        self.gw_a = Gateway.objects.create(gateway_mac="GWAAA", store=self.store_a, estation_id="E01")
        self.gw_b = Gateway.objects.create(gateway_mac="GWBBB", store=self.store_b, estation_id="E02")
        from core.models import MQTTMessage
        self.msg_a = MQTTMessage.objects.create(estation_id="E01", topic="test", data="{}", direction="sent")
        self.msg_b = MQTTMessage.objects.create(estation_id="E02", topic="test", data="{}", direction="sent")

    def test_staff_store_isolation_vulnerability(self):
        """
        VULNERABILITY: Staff users might see tags from stores they are not assigned to
        if CompanySecurityMixin only filters by store for 'manager' role.
        """
        self.client.login(username='staff_user', password='password123')

        # Set active store to Store A
        session = self.client.session
        session['active_store_id'] = self.store_a.id
        session.save()

        # Access the ESL Tag list
        response = self.client.get(reverse('admin:core_esltag_changelist'))

        self.assertEqual(response.status_code, 200)

        # If vulnerable, tag_b will be in the queryset
        tags = response.context['cl'].queryset

        # Verify if tag_b (from Store B) is visible to staff_user (only assigned to Store A)
        self.assertFalse(tags.filter(id=self.tag_b.id).exists(),
                         "SECURITY GAP: Staff user can see tags from Store B even though they are only assigned to Store A.")

    def test_staff_product_isolation_vulnerability(self):
        """
        VULNERABILITY: Staff users might see products from stores they are not assigned to.
        """
        self.client.login(username='staff_user', password='password123')

        # Access the Product list
        response = self.client.get(reverse('admin:core_product_changelist'))

        self.assertEqual(response.status_code, 200)

        # If vulnerable, product_b will be in the queryset
        products = response.context['cl'].queryset

        self.assertFalse(products.filter(store=self.store_b).exists(),
                         "SECURITY GAP: Staff user can see products from Store B.")

    def test_template_lab_access_restriction(self):
        """
        VULNERABILITY: Staff users should not access Template Design Lab
        """
        self.client.login(username='staff_user', password='password123')

        # Set active store
        session = self.client.session
        session['active_store_id'] = self.store_a.id
        session.save()

        response = self.client.get(reverse('sais_admin:template-gallery'))

        # It should be restricted (e.g. 403 or redirect)
        self.assertEqual(response.status_code, 403, "SECURITY GAP: Staff user can access Template Design Lab.")

    def test_mock_render_access_restriction(self):
        """
        VULNERABILITY: Staff users should not access mock render view
        """
        self.client.login(username='staff_user', password='password123')

        response = self.client.get(reverse('sais_admin:template-render', args=[self.spec.id]))

        self.assertEqual(response.status_code, 403, "SECURITY GAP: Staff user can access Mock Render View.")

    def test_staff_mqtt_isolation_vulnerability(self):
        """
        VULNERABILITY: Staff users might see MQTT messages from stores they are not assigned to.
        """
        self.client.login(username='staff_user', password='password123')

        # Access the MQTT Message list
        response = self.client.get(reverse('admin:core_mqttmessage_changelist'))

        self.assertEqual(response.status_code, 200)

        # If vulnerable, msg_b will be in the queryset
        messages = response.context['cl'].queryset

        self.assertFalse(messages.filter(id=self.msg_b.id).exists(),
                         "SECURITY GAP: Staff user can see MQTT messages from Store B.")
