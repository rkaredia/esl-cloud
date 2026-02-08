from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.shortcuts import render, redirect, get_object_or_404
from django import forms
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.db.models import Count, Q, Case, When, Value, IntegerField
from django.db import models

# Model and Task Imports

from .models import Company, User, Store, Gateway, Product, ESLTag, TagHardware 
from core.tasks import update_tag_image_task  

import os
from django.core.files.storage import default_storage
from django.conf import settings

import openpyxl

from decimal import Decimal, InvalidOperation
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from .views import download_tag_template, preview_tag_import, preview_product_import
# =================================================================
# 1. SHARED ACTIONS
# =================================================================

@admin.action(description='Regenerate images in background (Celery)')
def regenerate_product_tags(modeladmin, request, queryset):
    """Sends selected Products or Tags to Celery for background processing."""
    count = 0
    for item in queryset:
        if isinstance(item, Product):
            tags = item.esl_tags.all()
            for tag in tags:
                update_tag_image_task.delay(tag.id)
                count += 1
        elif isinstance(item, ESLTag):
            update_tag_image_task.delay(item.id)
            count += 1
    modeladmin.message_user(request, f"Queued {count} background tasks for image refresh.")

# =================================================================
# 2. SHARED BASE CLASS & FILTERS
# =================================================================



class StoreSpecificGatewayFilter(admin.SimpleListFilter):
    title = 'Gateway'
    parameter_name = 'gateway'

    def lookups(self, request, model_admin):
        if hasattr(request, 'active_store') and request.active_store:
            gateways = Gateway.objects.filter(store=request.active_store)
            return [(g.id, str(g)) for g in gateways]
        return []

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(gateway_id=self.value())
        return queryset

class CompanySecurityMixin:

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        
        # If the "Global" store is set, filter everything by it automatically
        if hasattr(request, 'active_store') and request.active_store:
            if hasattr(self.model, 'store'):
                return qs.filter(store=request.active_store)
            if self.model == ESLTag:
                return qs.filter(gateway__store=request.active_store)
        
        # Fallback for Superusers to see everything if no store is selected
        if request.user.is_superuser:
            return qs

        return qs.none()


        # 1. Allow everyone to see TagHardware (Global Specs)
        if self.model == TagHardware:
            return qs

        # 2. Security for the User model
        if self.model == User:
            return qs.filter(company=request.user.company)

        # 3. Security for the Company model itself
        if self.model == Company:
            return qs.filter(id=request.user.company_id)
            
        # 4. Security for Store model
        if self.model == Store:
            return qs.filter(company=request.user.company)
            
        # 5. Security for everything else (Product, Tag, Gateway)
        if hasattr(self.model, 'store'):
            return qs.filter(store__company=request.user.company)
        
        if self.model == ESLTag:
            return qs.filter(gateway__store__company=request.user.company)

        return qs

class BaseStoreAdmin(CompanySecurityMixin, admin.ModelAdmin):
    list_per_page = 100
    show_full_result_count = False

    class Media:
        css = { 'all': ('admin/css/custom_admin.css',) }
        js = ('admin/js/admin_enhancements.js',)

    def display_last_updated(self, obj):
        if not obj.last_updated:
            return "-"
        return format_html(
            '<span class="local-datetime" data-utc="{}">{}</span>',
            obj.last_updated.isoformat(),
            obj.last_updated.strftime("%d %b %Y, %I:%M %p")
        )
    display_last_updated.short_description = "Last Updated"
    display_last_updated.admin_order_field = 'last_updated'

# =================================================================
# 3. Tag Hardware ADMIN
# =================================================================

@admin.register(TagHardware)
class TagHardwareAdmin(admin.ModelAdmin):
    list_display = ('model_number', 'display_size_inch', 'width_px', 'height_px', 'color_scheme')
    search_fields = ('model_number',)

    def has_module_permission(self, request):
        return True

    def has_view_permission(self, request, obj=None):
        return True
    
    # Optional: Only let Superadmins edit the actual hardware specs
    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

# =================================================================
# 3. PRODUCT ADMIN
# =================================================================

@admin.register(Product)
class ProductAdmin(BaseStoreAdmin):
    
    # Helpers defined before they are called
    def display_store(self, obj):
        return format_html('<b style="color: #2563eb;">{}</b>', obj.store.name if obj.store else "N/A")
    display_store.short_description = "Store"
    # Don't show these fields in the 'Add' or 'Change' forms
    exclude = ('updated_by', 'store')

    def large_tag_preview(self, obj):
        tag = obj.esl_tags.first()
        if tag and tag.tag_image:
            return format_html('<img src="{}" style="width: 400px; border: 1px solid #ccc; border-radius: 8px;"/>', tag.tag_image.url)
        return "No image generated."
    large_tag_preview.short_description = "Tag Preview"

    list_display = ('image_status', 'sku', 'name', 'price', 'display_store', 'sync_tag_action', 'display_last_updated')   
    list_filter = ('price','last_updated')

    readonly_fields = ['display_store', 'display_last_updated', 'store']

    def sync_tag_action(self, obj):
        # This creates the button that triggers your celery task
        url = reverse('admin:sync-tag-manual', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}" style="padding: 2px 8px; background-color: #2563eb; color: white;">Sync</a>',
            url
        )
    sync_tag_action.short_description = "Action"

    search_fields = ('sku', 'name')
    actions = [regenerate_product_tags]
    ordering = ('sku',)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('store')
        if not hasattr(request, 'active_store') or not request.active_store:
            return qs.none()
        
        return qs.filter(store=request.active_store).annotate(
            has_tag_image=Count(
                'esl_tags', 
                filter=Q(
                    esl_tags__tag_image__gt='',
                    esl_tags__gateway__store=request.active_store 
                )
            )
        )

    def image_status(self, obj):
        if getattr(obj, 'has_tag_image', 0) > 0:
            return mark_safe('<span style="color: #059669; font-weight: bold;">● Generated</span>')
        if obj.esl_tags.filter(gateway__store=obj.store).exists():
            return mark_safe('<span style="color: #ea580c;">● Pending</span>')
        return mark_safe('<span style="color: #94a3b8;">○ No Tag</span>')
    image_status.admin_order_field = 'has_tag_image'
    image_status.short_description = "Status"

# 1. Provide initial data so the form doesn't see 'store' as empty
    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if hasattr(request, 'active_store'):
            initial['store'] = request.active_store
        return initial

    def save_model(self, request, obj, form, change):
        # 2. Assign values to the object instance
        obj.updated_by = request.user
        
        # We check both 'store' and 'active_store' as your middleware might use either
        if not change:
            if hasattr(request, 'active_store') and request.active_store:
                obj.store = request.active_store
            elif hasattr(request, 'store') and request.store:
                obj.store = request.store
            elif hasattr(request.user, 'userprofile') and request.user.userprofile.current_store:
                obj.store = request.user.userprofile.current_store

        # 3. CRITICAL: Only call super() AFTER the store is set
        super().save_model(request, obj, form, change)

        # 4. Background Tasks
        linked_tags = obj.esl_tags.all()
        if linked_tags.exists():
            for tag in linked_tags:
                update_tag_image_task.delay(tag.id)
            messages.info(request, f"Background update started for {linked_tags.count()} tag(s).")


    change_list_template = "admin/core/product/change_list.html" # Tells Django where the button is

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            # Map both potential names to the new modular view
            path('import-modisoft/', self.admin_site.admin_view(preview_product_import), name='import-modisoft'),
            path('import-mapping/', self.admin_site.admin_view(preview_product_import), name='import-tag-mapping-alias'),
        ]
        return custom_urls + urls

# =================================================================
# 4. TAG ADMIN
# =================================================================

@admin.register(ESLTag)
class TagAdmin(BaseStoreAdmin):

    change_list_template = "admin/core/esltag/change_list.html"
    list_filter_sheet = True

    def get_urls(self):
        def wrap(view):
            def wrapper(*args, **kwargs):
                return self.admin_site.admin_view(view)(*args, **kwargs)
            return wrapper

        custom_urls = [
            path('download-template/', wrap(download_tag_template), name='download_tag_template'),
            path('import-preview/', wrap(preview_tag_import), name='preview_tag_import'),
            path('<path:object_id>/sync/', self.admin_site.admin_view(self.manual_sync_view), name='sync-tag-manual'),
        ]
        urls = super().get_urls()
        return custom_urls + urls

    # --- UPDATED HELPERS FOR NEW ARCHITECTURE ---
    def display_store(self, obj):
        return obj.gateway.store.name if obj.gateway and obj.gateway.store else "Unassigned"
    display_store.short_description = "Store"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Only filter if we have an active_store in the session
        if hasattr(request, 'active_store') and request.active_store:
            if db_field.name == "gateway":
                # Only show gateways belonging to the currently selected store
                kwargs["queryset"] = Gateway.objects.filter(store=request.active_store)
            
            if db_field.name == "paired_product":
                # Only show products belonging to the currently selected store
                kwargs["queryset"] = Product.objects.filter(store=request.active_store)
        
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_model_number(self, obj):
        return obj.hardware_spec.model_number if obj.hardware_spec else "-"
    get_model_number.short_description = "Model P/N"

    def get_color_scheme(self, obj):
        return obj.hardware_spec.color_scheme if obj.hardware_spec else "-"
    get_color_scheme.short_description = "Colors"

    def resolution(self, obj):
        if obj.hardware_spec:
            return f"{obj.hardware_spec.width_px}×{obj.hardware_spec.height_px}"
        return "-"
    resolution.short_description = "Resolution"

    def large_tag_preview(self, obj):
        if obj.tag_image:
            return format_html('<img src="{}" style="width:400px; border-radius:8px; border:1px solid #ccc;"/>', obj.tag_image.url)
        return "No image generated."
    large_tag_preview.short_description = "Tag Preview"

    def battery_percentage(self, obj):
        val = obj.battery_level if obj.battery_level is not None else 0
        color = "#059669" if val > 50 else "#ea910c" if val > 20 else "#dc2626"
        return format_html('<span style="color: {}; font-weight: bold;">{}%</span>', color, val)
    battery_percentage.short_description = "Battery"
    battery_percentage.admin_order_field = 'battery_level'

    def image_status(self, obj):
        if not obj.paired_product:
            return mark_safe('<span style="color:#94a3b8;">○ No Product</span>')
        color = "#059669" if obj.tag_image else "#ea580c"
        label = "Generated" if obj.tag_image else "Pending"
        return mark_safe(f'<span style="color:{color};font-weight:bold;">● {label}</span>')
    image_status.admin_order_field = 'status_weight'
    image_status.short_description = "Status"

    # --- UPDATED CONFIG ---
    search_fields = ('tag_mac', 'paired_product__sku', 'paired_product__name', 'aisle')
    autocomplete_fields = ['paired_product']
    
    # Removed model_name and color_type, added helper methods
    list_display = (
        'image_status', 'tag_mac', 'get_model_number', 'resolution', 
        'get_color_scheme', 'display_store', 'aisle', 'battery_percentage',
        'sync_tag_action', 'display_last_updated', 'updated_by'
    )
    
    def sync_tag_action(self, obj):
        # This creates the button that triggers your celery task
        url = reverse('admin:sync-tag-manual', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}" style="padding: 2px 8px; background-color: #2563eb; color: white;">Sync</a>',
            url
        )
    sync_tag_action.short_description = "Action"

    list_filter = (StoreSpecificGatewayFilter, 'battery_level', 'last_updated')
    readonly_fields = ['tag_image', 'large_tag_preview', 'display_last_updated', 'updated_by']

    # Updated fieldsets to include new Location fields and Hardware Spec
    fieldsets = (
        ('Hardware Identity', {'fields': ('tag_mac', 'gateway', 'hardware_spec', 'battery_level')}),
        ('Product Linkage', {'description': 'Type SKU or Name to filter products...', 'fields': ('paired_product',)}),
        ('Store Location', {'fields': ('aisle', 'section', 'shelf_row')}),
        ('Visual Assets', {'fields': ('large_tag_preview',)}),
        ('Audit Trail', {'classes': ('collapse',), 'fields': ('updated_by', 'display_last_updated')}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('gateway__store', 'paired_product', 'updated_by', 'hardware_spec')
        if hasattr(request, 'active_store') and request.active_store:
            qs = qs.filter(gateway__store=request.active_store)
        else:
            return qs.none()

        return qs.annotate(
            status_weight=Case(
                When(paired_product__isnull=False, then=Value(1)),
                When(paired_product__isnull=True, then=Value(2)),
                output_field=IntegerField(),
            )
        )

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        if obj.paired_product:
            try:
                update_tag_image_task.delay(obj.id)
                messages.info(request, "Background image generation started.")
            except Exception as e:
                messages.warning(request, f"Task queued failed. Error: {e}")        

    def manual_sync_view(self, request, object_id):
        obj = get_object_or_404(ESLTag, pk=object_id)
        update_tag_image_task.delay(obj.id)
        messages.success(request, f"Manual sync for {obj.tag_mac} sent to background worker.")
        return redirect(request.META.get('HTTP_REFERER', 'admin:core_esltag_changelist'))

# =================================================================
# 5. REMAINING ADMINS
# =================================================================

class ESLTagInline(admin.TabularInline):
    model = ESLTag
    extra = 1
    fields = ('tag_mac', 'hardware_spec', 'paired_product', 'aisle')
    autocomplete_fields = ['paired_product']

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if hasattr(request, 'active_store') and request.active_store:
            if db_field.name == "paired_product":
                kwargs["queryset"] = Product.objects.filter(store=request.active_store)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Gateway)
class GatewayAdmin(BaseStoreAdmin):
    list_display = ('gateway_mac', 'store', 'is_online', 'display_last_updated')
    inlines = [ESLTagInline] # Add this line
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not hasattr(request, 'active_store') or not request.active_store:
            return qs.none()
        return qs.filter(store=request.active_store)

@admin.register(Store)
class StoreAdmin(BaseStoreAdmin): # Changed
    list_display = ('name', 'company', 'location_code')

@admin.register(Company)
class CompanyAdmin(BaseStoreAdmin): # Changed
    list_display = ('name', 'owner_name', 'contact_email', 'created_at')
    search_fields = ('name', 'owner_name')
    pass

@admin.register(User)
class CustomUserAdmin(UserAdmin, BaseStoreAdmin):
    list_display = ('username', 'email', 'company', 'role', 'is_staff')
    list_filter = ('company', 'role', 'is_staff')

    # 1. VISIBILITY: Inherits get_queryset from CompanySecurityMixin
    # This ensures Owners only see users in their own company.
    filter_horizontal = ('groups', 'managed_stores')
    # 2. HIDE PERMISSIONS: Remove individual permission boxes for non-admins
    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        
        # Inject "Managed Stores" into the 'Personal Info' or a new section
        # We search for the 'Permissions' section to add it there
        for section_title, section_info in fieldsets:
            if section_title == 'Permissions':
                fields = list(section_info['fields'])
                if 'managed_stores' not in fields:
                    fields.append('managed_stores')
                section_info['fields'] = tuple(fields)

        if not request.user.is_superuser:
            # Hide sensitive fields for non-admins
            fieldsets = [
                (name, {
                    'fields': [f for f in info['fields'] if f not in ['user_permissions', 'is_superuser', 'is_staff']]
                }) for name, info in fieldsets
            ]
        return fieldsets

    # 3. LOCK COMPANY: Pre-select company and make it read-only for non-admins
    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        if not request.user.is_superuser:
            # Owners/Managers cannot change the company or their own role
            return readonly + ('company', 'role')
        return readonly

    # 4. AUTO-ASSIGN COMPANY: Ensure the DB saves the correct company
    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            # Force the new user to the creator's company
            obj.company = request.user.company
            
            # PREVENTION: If a Manager tries to create an Owner, force it back to Manager
            if not change and obj.role == 'owner':
                obj.role = 'manager'
                messages.warning(request, "You cannot create an Owner account. Role set to Manager.")
        
        super().save_model(request, obj, form, change)

    # 5. LIMIT GROUPS: Filter which roles (Groups) can be assigned
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        # --- 1. HANDLE GROUPS (Roles) ---
        if db_field.name == "groups" and not request.user.is_superuser:
            if request.user.role == 'owner':
                kwargs["queryset"] = Group.objects.filter(name__in=['Store Manager', 'Store Staff', 'Read Only'])
            elif request.user.role == 'manager':
                kwargs["queryset"] = Group.objects.filter(name__in=['Store Staff', 'Read Only'])

        # --- 2. HANDLE MANAGED STORES ---
        if db_field.name == "managed_stores" and not request.user.is_superuser:
            if request.user.role == 'owner':
                # Owners see all stores in their company
                kwargs["queryset"] = Store.objects.filter(company=request.user.company)
            elif request.user.role == 'manager':
                # Managers ONLY see stores they are personally assigned to
                kwargs["queryset"] = request.user.managed_stores.all()
            else:
                # Staff or Read-only should see nothing (safety)
                kwargs["queryset"] = Store.objects.none()

        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def has_delete_permission(self, request, obj=None):
        # 1. Superusers can delete anyone
        if request.user.is_superuser:
            return True
        
        # 2. If we are looking at a specific user (obj)
        if obj:
            # Prevent Managers from deleting Owners or other Managers
            if request.user.role == 'manager':
                return False 
            
            # Prevent Owners from deleting themselves or Superusers
            if request.user.role == 'owner':
                if obj.is_superuser or obj == request.user:
                    return False
                return True

        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Completely remove 'delete_selected' for Managers
        if not request.user.is_superuser and request.user.role == 'manager':
            if 'delete_selected' in actions:
                del actions['delete_selected']
        return actions        