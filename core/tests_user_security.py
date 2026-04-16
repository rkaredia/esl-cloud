from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import Group
from core.models import Company, Store, User
from core.admin.organisation import CustomUserAdmin
from core.admin.base import admin_site

class UserPrivilegeEscalationTest(TestCase):
    def setUp(self):
        # 1. Setup Company and Stores
        self.company = Company.objects.create(name="Test Company")
        self.store_a = Store.objects.create(name="Store A", company=self.company)
        self.store_b = Store.objects.create(name="Store B", company=self.company)

        # 2. Setup Roles/Groups (simulating seed_data)
        self.manager_group, _ = Group.objects.get_or_create(name='Store Manager')
        # In a real environment, we'd need to attach permissions,
        # but for unit testing ModelAdmin methods directly, we can just mock the user.

        # 3. Create a Manager user assigned only to Store A
        self.manager = User.objects.create_user(
            username='manager_a',
            password='password123',
            company=self.company,
            role='manager',
            is_staff=True
        )
        self.manager.groups.add(self.manager_group)
        self.manager.managed_stores.add(self.store_a)

    def test_manager_role_choices_escalation(self):
        """
        Verify that a manager cannot see 'owner' as a role choice.
        """
        ma = CustomUserAdmin(User, admin_site)

        # Simulate the request
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get(reverse('admin:core_user_change', args=[self.manager.id]))
        request.user = self.manager

        # Get the form field for 'role'
        formfield = ma.formfield_for_choice_field(User._meta.get_field('role'), request)
        choices = [c[0] for c in formfield.choices]

        # SECURE STATE: 'owner' and 'admin' should NOT be in choices
        self.assertNotIn('owner', choices, "VULNERABILITY: Manager can see 'owner' role choice")
        self.assertNotIn('admin', choices)
        # They should see 'manager', 'staff', 'readonly'
        self.assertIn('manager', choices)

    def test_manager_store_queryset_escalation(self):
        """
        Verify that a manager cannot see Store B in managed_stores,
        as they are only assigned to Store A.
        """
        ma = CustomUserAdmin(User, admin_site)

        # Simulate the request
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get(reverse('admin:core_user_change', args=[self.manager.id]))
        request.user = self.manager

        # Get the form field for 'managed_stores'
        formfield = ma.formfield_for_manytomany(User._meta.get_field('managed_stores'), request)
        queryset = formfield.queryset

        # SECURE STATE: Store B should NOT be in the queryset
        self.assertNotIn(self.store_b, queryset, "VULNERABILITY: Manager can see Store B in managed_stores choices")
        self.assertIn(self.store_a, queryset)
