from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.db.models import Q
from .base import admin_site, CompanySecurityMixin
from ..models import Company, Store, User

@admin.register(Company, site=admin_site)
class CompanyAdmin(CompanySecurityMixin, admin.ModelAdmin):
    """Admin for Managing Companies."""
    list_display = ('name', 'contact_email', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('contact_email', 'is_active')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(id=request.user.company_id)

    def has_add_permission(self, request): return request.user.is_superuser
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser

@admin.register(Store, site=admin_site)
class StoreAdmin(CompanySecurityMixin, admin.ModelAdmin):
    """Admin for Managing Stores."""
    list_display = ('name', 'company', 'location_code', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('location_code', 'is_active')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')

    def get_readonly_fields(self, request, obj=None):
        return ('company',) if not request.user.is_superuser else ()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "company" and not request.user.is_superuser:
            kwargs["queryset"] = Company.objects.filter(id=request.user.company_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(User, site=admin_site)
class CustomUserAdmin(UserAdmin, CompanySecurityMixin):
    """Extended User Admin with Role and Store assignments."""
    list_display = ('username', 'company', 'role', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (
        ('Store Allocation', {'fields': ('managed_stores', 'company', 'role')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Store Allocation', {'fields': ('managed_stores', 'company', 'role')}),
    )
    filter_horizontal = ('managed_stores',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser: return qs

        qs = qs.filter(company=request.user.company)
        if request.user.role == 'owner': return qs.exclude(is_superuser=True)
        if request.user.role == 'manager':
            return qs.filter(
                Q(role__in=['manager', 'readonly']) &
                Q(managed_stores__in=request.user.managed_stores.all())
            ).distinct()
        return qs.filter(id=request.user.id)

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            obj.company = request.user.company
        super().save_model(request, obj, form, change)

        # Auto-assign groups based on role
        role_map = {'owner': 'Owner', 'manager': 'Store Manager', 'readonly': 'Read Only'}
        group, _ = Group.objects.get_or_create(name=role_map.get(obj.role, 'Store Staff'))
        obj.groups.clear()
        obj.groups.add(group)
