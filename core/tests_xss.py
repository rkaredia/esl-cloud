from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from core.models import GlobalSetting, Company, Store
from core.admin.base import GlobalSettingAdmin, admin_site

User = get_user_model()

class GlobalSettingXSSTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(username='admin', password='password123', email='admin@example.com')
        self.client.login(username='admin', password='password123')

        # Create a store to satisfy StoreContextMiddleware
        self.company = Company.objects.create(name="Test Company")
        self.store = Store.objects.create(name="Test Store", company=self.company)

        session = self.client.session
        session['active_store_id'] = self.store.id
        session.save()

        self.malicious_value = '<script>alert("XSS")</script>'
        self.setting = GlobalSetting.objects.create(key='test_xss', value=self.malicious_value)

    def test_value_display_escapes_html(self):
        """
        Tests that GlobalSettingAdmin.value_display properly escapes HTML to prevent XSS.
        """
        # Get the change list view which uses value_display
        url = reverse('sais_admin:core_globalsetting_changelist')
        response = self.client.get(url)

        # Check if the malicious script is in the response unescaped
        # If it's unescaped, mark_safe is being used dangerously.
        # Note: &lt;script&gt; is the escaped version.
        self.assertContains(response, '&lt;script&gt;alert(&quot;XSS&quot;)&lt;/script&gt;')
        self.assertNotContains(response, self.malicious_value)
