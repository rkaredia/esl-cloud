from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.db.models import Q
from .base import admin_site, CompanySecurityMixin
from ..models import Company, Store, User
import logging

"""
ORGANISATION & USER ADMIN
-------------------------
Manages the 'Tenant' hierarchy: Companies, Stores, and the Users
who access them.

Key Concepts:
- HIERARCHY: Company > Store > User.
- PERMISSIONS: Uses 'get_queryset' to ensure that even a Company Owner
  cannot see or edit users from another company.
- ROLE-BASED ACCESS (RBAC): Automatically assigns users to Django
  Permission Groups based on the 'Role' dropdown (e.g., 'Manager' role
  gets 'Store Manager' group).
"""

logger = logging.getLogger(__name__)

@admin.register(Company, site=admin_site)
class CompanyAdmin(CompanySecurityMixin, admin.ModelAdmin):
    """
    TENANT ADMINISTRATION
    ---------------------
    Represents the business entities. Only system superusers can create new
    companies, but company owners can see their own company profile.
    """
    list_display = ('name', 'contact_email', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('contact_email', 'is_active')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')

    def get_queryset(self, request):
        """Security: Restrict non-superusers to their own company record."""
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(id=request.user.company_id)

    def has_add_permission(self, request): return request.user.is_superuser
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser

@admin.register(Store, site=admin_site)
class StoreAdmin(CompanySecurityMixin, admin.ModelAdmin):
    """
    PHYSICAL LOCATION ADMINISTRATION
    --------------------------------
    Manages the individual stores within a company.
    """
    list_display = ('name', 'company', 'location_code', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('location_code', 'is_active')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')

    def get_readonly_fields(self, request, obj=None):
        """Prevent managers from moving a store to a different company."""
        return ('company',) if not request.user.is_superuser else ()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Ensure that when adding a store, you can only pick your own company."""
        if db_field.name == "company" and not request.user.is_superuser:
            kwargs["queryset"] = Company.objects.filter(id=request.user.company_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(User, site=admin_site)
class CustomUserAdmin(UserAdmin, CompanySecurityMixin):
    """
    USER & PERMISSION MANAGEMENT
    ----------------------------
    Extends the standard Django UserAdmin with custom multi-tenant fields.
    """
    list_display = ('username', 'company', 'role', 'is_staff')

    # fieldsets organize the layout of the Edit User page
    fieldsets = UserAdmin.fieldsets + (
        ('Store Allocation', {'fields': ('managed_stores', 'company', 'role')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Store Allocation', {'fields': ('managed_stores', 'company', 'role')}),
    )

    # horizontal filter provides a 'Double List Box' for picking multiple stores
    filter_horizontal = ('managed_stores',)

    def get_fieldsets(self, request, obj=None):
        """
        SECURITY: HIDE SENSITIVE FIELDS
        ------------------------------
        Removes administrative permission fields from the form for non-superusers.
        """
        fieldsets = super().get_fieldsets(request, obj)
        if not request.user.is_superuser:
            sensitive_fields = ['is_superuser', 'is_staff', 'groups', 'user_permissions']
            new_fieldsets = []
            for name, opts in fieldsets:
                fields = list(opts.get('fields', []))
                # Filter out sensitive fields from every fieldset
                filtered_fields = [f for f in fields if f not in sensitive_fields]
                if filtered_fields:
                    # Create a copy of opts and update fields to preserve other keys (like classes)
                    new_opts = opts.copy()
                    new_opts['fields'] = filtered_fields
                    new_fieldsets.append((name, new_opts))
            return tuple(new_fieldsets)
        return fieldsets

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        """Security: Prevent non-superusers from creating 'Global Admin' roles."""
        if db_field.name == "role" and not request.user.is_superuser:
            kwargs["choices"] = [c for c in User.ROLE_CHOICES if c[0] != 'admin']
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Security: Restrict company selection to the user's own tenant."""
        if db_field.name == "company" and not request.user.is_superuser:
            kwargs["queryset"] = Company.objects.filter(id=request.user.company_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        """Security: Restrict store selection to the user's company."""
        if db_field.name == "managed_stores" and not request.user.is_superuser:
            kwargs["queryset"] = Store.objects.filter(company_id=request.user.company_id)
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def get_queryset(self, request):
        """
        SECURITY: USER ISOLATION
        ------------------------
        - Superusers see everyone.
        - Owners see everyone in their company.
        - Managers see only their staff in their assigned stores.
        """
        try:
            qs = super().get_queryset(request)
            if request.user.is_superuser: return qs

            qs = qs.filter(company=request.user.company)
            if request.user.role == 'owner': return qs.exclude(is_superuser=True)

            if request.user.role == 'manager':
                # Filter by role AND overlapping store assignments
                return qs.filter(
                    Q(role__in=['manager', 'readonly']) &
                    Q(managed_stores__in=request.user.managed_stores.all())
                ).distinct()

            return qs.filter(id=request.user.id)
        except Exception:
            logger.exception("Error in CustomUserAdmin.get_queryset")
            return User.objects.none()

    def save_model(self, request, obj, form, change):
        """
        AUTO-PERMISSION LOGIC
        ---------------------
        Assigns the user to the correct Django Permission Group based
        on their selected Role.
        """
        try:
            if not request.user.is_superuser:
                obj.company = request.user.company
            super().save_model(request, obj, form, change)

            # Assign to Group: Owner, Store Manager, Store Staff, or Read Only
            role_map = {'owner': 'Owner', 'manager': 'Store Manager', 'readonly': 'Read Only'}
            group_name = role_map.get(obj.role, 'Store Staff')
            group, _ = Group.objects.get_or_create(name=group_name)

            obj.groups.clear()
            obj.groups.add(group)
        except Exception as e:
            logger.exception("Error in CustomUserAdmin.save_model")
            raise e
