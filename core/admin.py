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

from core.tasks import update_tag_image_task  # Restored task import

# =================================================================
# 1. MIXINS & SECURITY
# =================================================================
class AuditAdminMixin:
    """
    Automatically sets the updated_by field to the current logged-in user.
    """
    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

@admin.action(description='Regenerate images in background (Celery)')
def regenerate_images_action(modeladmin, request, queryset):
    count = 0
    for item in queryset:
        if isinstance(item, Product):
            for tag in item.esl_tags.all():
                update_tag_image_task.delay(tag.id)
                count += 1
        elif isinstance(item, ESLTag):
            update_tag_image_task.delay(item.id)
            count += 1
    modeladmin.message_user(request, f"Queued {count} background tasks for image refresh.")

class CompanySecurityMixin(AuditAdminMixin):
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
#    def display_last_updated(self, obj):
#        if not obj.last_updated: return "-"
#        return format_html('<span class="local-datetime" data-utc="{}">{}</span>',
#                           obj.last_updated.isoformat(), obj.last_updated.strftime("%d %b %Y"))
#    display_last_updated.short_description = "Last Updated"

    def sync_button(self, obj):
        url = reverse('admin:sync-tag-manual', args=[obj.pk])
        return format_html('<a class="button" href="{}" style="background:#2563eb; color:white;">Sync</a>', url)
    sync_button.short_description = "Action"

# =================================================================
# 1. COMPANY ADMIN
# =================================================================
@admin.register(Company)
class CompanyAdmin(CompanySecurityMixin, admin.ModelAdmin):
    list_display = ('name', 'contact_email', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('contact_email', 'is_active')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')


# =================================================================
# 2. STORE ADMIN
# =================================================================
@admin.register(Store)
class StoreAdmin(CompanySecurityMixin, admin.ModelAdmin):
    list_display = ('name', 'company', 'location_code', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('location_code', 'is_active')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')

# =================================================================
# 3. GATEWAY ADMIN
# =================================================================
@admin.register(Gateway)
class GatewayAdmin(CompanySecurityMixin, admin.ModelAdmin):
    list_display = ('gateway_mac', 'store', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('store', 'is_active')
    readonly_fields = ( 'created_at', 'updated_at', 'updated_by')

# =================================================================
# 3. TAG HARDWARE ADMIN
# =================================================================
@admin.register(TagHardware)
class TagHardwareAdmin(admin.ModelAdmin):
    list_display = ('model_number', 'width_px', 'height_px', 'color_scheme', 'display_size_inch', 'created_at', 'updated_at', 'updated_by')
    def has_change_permission(self, request, obj=None): return request.user.is_superuser

# =================================================================
# 2. Product ADMIN
# =================================================================


@admin.register(Product)
class ProductAdmin(CompanySecurityMixin, UIHelperMixin, admin.ModelAdmin):


    list_display = ('image_status', 'sku', 'name', 'price', 'store', 'sync_button', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('name', 'price')
    search_fields = ('sku', 'name')
    actions = [regenerate_images_action]
    readonly_fields = ('updated_at', 'updated_by', 'image_preview_large','image_status')

    change_list_template = "admin/core/product/change_list.html"
    actions = ['regenerate_product_images', 'sync_products']

    @admin.action(description="Regenerate Tag Images for selected products")
    def regenerate_product_images(self, request, queryset):
        # Trigger your image generation logic here
        self.message_user(request, f"Image regeneration started for {queryset.count()} products.")

    @admin.action(description="Sync selected products to Gateways")
    def sync_products(self, request, queryset):
        # Trigger MQTT/Sync logic
        self.message_user(request, f"Sync command sent for {queryset.count()} products.")

    def image_status(self, obj):
        # Restore: Green/Orange/Grey logic
        has_image = obj.esl_tags.filter(tag_image__gt='').exists()
        has_tag = obj.esl_tags.exists()
        
        if has_image:
            return mark_safe('<span style="color: #059669; font-weight: bold;">● Generated</span>')
        if has_tag:
            return mark_safe('<span style="color: #ea580c; font-weight: bold;">● Pending</span>')
        return mark_safe('<span style="color: #94a3b8;">○ No Tag</span>')
    image_status.short_description = "Status"

    def sync_button(self, obj):
        # Custom button for single-product sync
        return format_html(
            '<a class="button" href="#" onclick="alert(\'Syncing...\'); return false;" style="background-color: #2563eb; color: white; padding: 3px 10px;">Sync</a>'
        )
    sync_button.short_description = "Action"

    def image_preview_large(self, obj):
        tag = obj.esl_tags.first()
        if tag and tag.tag_image:
            return format_html('<img src="{}" style="max-width: 300px; border-radius: 8px;"/>', tag.tag_image.url)
        return "No image available"

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
class ESLTag(CompanySecurityMixin, UIHelperMixin, admin.ModelAdmin):
    #list_display = ('tag_mac', 'paired_product', 'hardware_spec', 'updated_at', 'created_at', 'updated_by')
    #list_editable = ('paired_product', 'hardware_spec')
    #readonly_fields = ('updated_at', 'created_at', 'updated_by')
    # AJAX Search for Product (Autocomplete)
    #autocomplete_fields = ['paired_product']    

    list_display = ('image_status', 'tag_mac', 'get_paired_info', 'battery_status', 'hardware_spec','sync_button', 'aisle', 'section', 'shelf_row', 'updated_at', 'created_at', 'updated_by')
    list_editable = ( 'hardware_spec', 'aisle', 'section', 'shelf_row')

    # 2. Restored AJAX Autocomplete (Search as you type)
    autocomplete_fields = ['paired_product']
    
    actions = [regenerate_images_action]
    readonly_fields = ('get_paired_info', 
        'image_preview_large', 
        'updated_at', 
        'updated_by', 
        'created_at')

    actions = ['regenerate_tag_images', 'sync_tags']
    change_list_template = "admin/core/esltag/change_list.html"


    fieldsets = (
        ('Hardware', {'fields': ('tag_mac', 'gateway', 'hardware_spec', 'battery_level')}),
        ('Pairing', {
            'description': 'Search for a product by SKU or Name below.',
            'fields': ('paired_product',),
        }),
        ('Visuals', {'fields': ('image_preview_large',)}),
        ('Location', {'fields': ('aisle', 'section', 'shelf_row')}),
        ('Audit', {'fields': ('updated_by', 'updated_at')}),
    )

    def get_paired_info(self, obj):
        # FIX: Returns "SKU - Name" instead of "Product object (4)"
        if obj.paired_product:
            return f"{obj.paired_product.sku} - {obj.paired_product.name}"
        return mark_safe('<i style="color: #94a3b8;">Unpaired</i>')
    get_paired_info.short_description = "Paired Product"

    def image_status(self, obj):
        if not obj.paired_product:
            return mark_safe('<span style="color:#94a3b8;">○ No Product</span>')
        color = "#059669" if obj.tag_image else "#ea580c"
        label = "Generated" if obj.tag_image else "Pending"
        return mark_safe(f'<span style="color:{color}; font-weight:bold;">● {label}</span>')
    image_status.short_description = "Status"

    def thumbnail(self, obj):
        if obj.tag_image:
            return format_html('<img src="{}" style="width: 50px; height: auto; border-radius: 4px;"/>', obj.tag_image.url)
        return "-"

    def battery_status(self, obj):
        val = obj.battery_level
        color = "#059669" if val > 50 else "#ea580c" if val > 20 else "#dc2626"
        return format_html('<b style="color: {};">{}%</b>', color, val)
    battery_status.short_description = "Battery"

    def image_preview_large(self, obj):
        if obj.tag_image:
            return format_html('<img src="{}" style="max-width: 400px; border: 2px solid #eee; border-radius: 12px;"/>', obj.tag_image.url)
        return "Waiting for background generation..."
    image_preview_large.short_description = "Current Tag Image"

    def get_product_name(self, obj):
        return f"{obj.paired_product.name} ({obj.paired_product.sku})" if obj.paired_product else "-"
    get_product_name.short_description = 'Paired Product'

    def tag_image_thumbnail(self, obj):
        if obj.tag_image:
            return format_html('<img src="{}" style="width: 50px; height: auto;" />', obj.tag_image.url)
        return "No Image"
    
    def tag_image_preview(self, obj):
        if obj.tag_image:
            return format_html('<img src="{}" style="max-width: 300px; height: auto;" />', obj.tag_image.url)
        return "No Image Preview Available"

    @admin.action(description="Regenerate Images for selected tags")
    def regenerate_tag_images(self, request, queryset):
        self.message_user(request, "Regenerating selected tags...")

    @admin.action(description="Sync selected tags to Gateway")
    def sync_tags(self, request, queryset):
        self.message_user(request, "Syncing tags...")





    def model_info(self, obj):
        return f"{obj.hardware_spec.model_number}" if obj.hardware_spec else "-"
    
    def battery_status(self, obj):
        val = obj.battery_level or 0
        color = "#059669" if val > 50 else "#dc2626"
        return format_html('<b style="color: {};">{}%</b>', color, val)

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




