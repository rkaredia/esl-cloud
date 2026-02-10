from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.db.models import Count, Q, Case, When, Value, IntegerField
from django.shortcuts import render, redirect, get_object_or_404

from .models import Company, User, Store, Gateway, Product, ESLTag, TagHardware 
from core.tasks import update_tag_image_task  
from .views import download_tag_template, preview_tag_import, preview_product_import

# =================================================================
# 1. MIXINS & SECURITY
# =================================================================

class CompanySecurityMixin:
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs

        # 1. Global Company Filter
        if hasattr(self.model, 'company'):
            qs = qs.filter(company=request.user.company)
        elif hasattr(self.model, 'store'):
            qs = qs.filter(store__company=request.user.company)
        elif self.model == ESLTag:
            qs = qs.filter(gateway__store__company=request.user.company)

        # 2. Manager Specific Store Filter
        if request.user.role == 'manager':
            assigned_stores = request.user.managed_stores.all()
            if hasattr(self.model, 'store'):
                qs = qs.filter(store__in=assigned_stores)
            elif self.model == Store:
                qs = qs.filter(id__in=assigned_stores.values_list('id', flat=True))
            elif self.model == ESLTag:
                qs = qs.filter(gateway__store__in=assigned_stores)

        return qs

class UIHelperMixin:
    """Shared display methods for cleaner list views."""
    def display_last_updated(self, obj):
        if not obj.last_updated: return "-"
        return format_html('<span class="local-datetime" data-utc="{}">{}</span>',
                           obj.last_updated.isoformat(), obj.last_updated.strftime("%d %b %Y"))
    display_last_updated.short_description = "Last Updated"

    def sync_button(self, obj):
        url = reverse('admin:sync-tag-manual', args=[obj.pk])
        return format_html('<a class="button" href="{}" style="background:#2563eb; color:white;">Sync</a>', url)
    sync_button.short_description = "Action"

# =================================================================
# 2. PRODUCT ADMIN
# =================================================================

@admin.register(Product)
class ProductAdmin(CompanySecurityMixin, UIHelperMixin, admin.ModelAdmin):
    list_display = ('image_status', 'sku', 'name', 'price', 'sync_button', 'display_last_updated')
    search_fields = ('sku', 'name')
    readonly_fields = ['display_last_updated', 'store']
    exclude = ('updated_by',)
    change_list_template = "admin/core/product/change_list.html"

    def image_status(self, obj):
        has_img = obj.esl_tags.filter(tag_image__gt='').exists()
        color = "#059669" if has_img else "#ea580c"
        label = "Generated" if has_img else "Pending"
        return mark_safe(f'<span style="color: {color}; font-weight: bold;">● {label}</span>')

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        if not change and hasattr(request, 'active_store'):
            obj.store = request.active_store
        super().save_model(request, obj, form, change)
        for tag in obj.esl_tags.all():
            update_tag_image_task.delay(tag.id)

    def get_urls(self):
        return [path('import-modisoft/', self.admin_site.admin_view(preview_product_import), name='import-modisoft')] + super().get_urls()

# =================================================================
# 3. ESL TAG ADMIN
# =================================================================

@admin.register(ESLTag)
class TagAdmin(CompanySecurityMixin, UIHelperMixin, admin.ModelAdmin):
    list_display = ('image_status', 'tag_mac', 'model_info', 'battery_status', 'sync_button')
    autocomplete_fields = ['paired_product']
    readonly_fields = ['tag_image', 'display_last_updated', 'updated_by']
    change_list_template = "admin/core/esltag/change_list.html"
    
    fieldsets = (
        ('Identity', {'fields': ('tag_mac', 'gateway', 'hardware_spec', 'battery_level')}),
        ('Linkage', {'fields': ('paired_product',)}),
        ('Location', {'fields': ('aisle', 'section', 'shelf_row')}),
    )

    def model_info(self, obj):
        return f"{obj.hardware_spec.model_number}" if obj.hardware_spec else "-"
    
    def battery_status(self, obj):
        val = obj.battery_level or 0
        color = "#059669" if val > 50 else "#dc2626"
        return format_html('<b style="color: {};">{}%</b>', color, val)

    def image_status(self, obj):
        color = "#059669" if obj.tag_image else "#ea580c"
        return mark_safe(f'<span style="color:{color};">● {"Generated" if obj.tag_image else "Pending"}</span>')

    def manual_sync_view(self, request, object_id):
        update_tag_image_task.delay(object_id)
        messages.success(request, "Sync task queued.")
        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def get_urls(self):
        custom = [
            path('<path:object_id>/sync/', self.admin_site.admin_view(self.manual_sync_view), name='sync-tag-manual'),
            path('import-preview/', self.admin_site.admin_view(preview_tag_import), name='preview_tag_import'),
        ]
        return custom + super().get_urls()

# =================================================================
# 4. SYSTEM & USER ADMIN
# =================================================================

@admin.register(User)
class CustomUserAdmin(UserAdmin, CompanySecurityMixin):
    list_display = ('username', 'company', 'role', 'is_staff')
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        
        # 1. Scope by Company
        qs = qs.filter(company=request.user.company)

        # 2. Scope by Role Hierarchy
        if request.user.role == 'owner':
            return qs.exclude(is_superuser=True)
        
        if request.user.role == 'manager':
            # Managers only see themselves and staff/read-only in their assigned stores
            return qs.filter(
                Q(role__in=['manager', 'readonly']) & 
                Q(managed_stores__in=request.user.managed_stores.all())
            ).distinct()

        return qs.filter(id=request.user.id)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            # 1. Store Access
            if db_field.name == "managed_stores":
                if request.user.role == 'owner':
                    kwargs["queryset"] = Store.objects.filter(company=request.user.company)
                else:
                    kwargs["queryset"] = request.user.managed_stores.all()
            
            # 2. Group/Role Assignment (Requirement: Owner can add Mgr/Staff, Mgr can add Staff)
            if db_field.name == "groups":
                allowed_groups = ['Read Only']
                if request.user.role == 'owner':
                    allowed_groups += ['Store Manager', 'Store Staff']
                elif request.user.role == 'manager':
                    allowed_groups += ['Store Staff']
                
                kwargs["queryset"] = Group.objects.filter(name__in=allowed_groups)

        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            obj.company = request.user.company
        super().save_model(request, obj, form, change)
        
        role_map = {'owner': 'Owner', 'manager': 'Store Manager', 'readonly': 'Read Only'}
        group_name = role_map.get(obj.role, 'Store Staff')
        group, _ = Group.objects.get_or_create(name=group_name)
        obj.groups.add(group)

@admin.register(Gateway)
class GatewayAdmin(CompanySecurityMixin, admin.ModelAdmin):
    list_display = ('gateway_mac', 'store', 'is_online')

@admin.register(TagHardware)
class TagHardwareAdmin(admin.ModelAdmin):
    list_display = ('model_number', 'display_size_inch', 'color_scheme')
    def has_change_permission(self, request, obj=None): return request.user.is_superuser

admin.site.register(Company)
admin.site.register(Store)