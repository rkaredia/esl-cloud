from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.db.models import Count, Q
from django.shortcuts import redirect,render
from django.conf import settings
import time
import json

from .models import Company, User, Store, Gateway, Product, ESLTag, TagHardware
from .views import download_tag_template, preview_tag_import, preview_product_import, bulk_map_tags_view
from core.tasks import update_tag_image_task
from django_celery_results.models import TaskResult, GroupResult
from django_celery_results.admin import GroupResultAdmin, TaskResultAdmin


# =================================================================
# CUSTOM ADMIN SITE
# =================================================================

class SAISAdminSite(admin.AdminSite):
#    """Custom admin site with SAIS branding and grouped menu."""

    site_header = "SAIS Platform Administration"
    site_title = "SAIS Admin"
    index_title = "Welcome to SAIS Control Panel"
    
    def each_context(self, request):
        context = super().each_context(request)
        context['custom_admin_css'] = mark_safe("""
            <style>
                :root {
                    --bg-sidebar: #f1f5f9;
                    --navy: #003459;
                    --dark-navy: #00171F;
                    --azure: #C7EFFF;
                    --white: #ffffff;
                    --light-gray: #f1f5f9;
                }
                #header { background: var(--navy); border-bottom: 3px solid var(--light-gray); }
                #branding h1 a { color: var(--white) !important; }
                #nav-sidebar .section {
                    color: var(--white) !important;
                    background: #417690 !important;
                    margin: 0 !important;
                    display: flex;
                    align-items: center;
                    border: none !important;
                }
                .app-inventory .section:before { content: "📦"; margin-right: 10px; }
                .app-hardware .section:before { content: "📡"; margin-right: 10px; }
                .app-organisation .section:before { content: "🏢"; margin-right: 10px; }
                .app-monitoring .section:before { content: "⚙️"; margin-right: 10px; }
                #nav-sidebar th a { color: var(--navy) !important; }
                #nav-sidebar .model-esltag th a:before { content: "🏷️ "; }
                #nav-sidebar .model-product th a:before { content: "🛒 "; }
                #nav-sidebar .model-gateway th a:before { content: "📟 "; }
                #nav-sidebar .model-taghardware th a:before { content: "🛠️ "; }
                #nav-sidebar .model-company th a:before { content: "🏭 "; }
                #nav-sidebar .model-store th a:before { content: "🏪 "; }
                #nav-sidebar .model-user th a:before { content: "👤 "; }
                #nav-sidebar .model-group th a:before { content: "👥 "; }
                #nav-sidebar .model-taskresult th a:before { content: "📊 "; }
                #nav-sidebar .model-groupresult th a:before { content: "📁 "; }
                #nav-sidebar .addlink {
                    background: var(--navy) !important;
                    color: var(--white) !important;
                    padding: 3px 8px !important;
                    border-radius: 4px;
                    text-transform: uppercase;
                    font-size: 10px;
                    background-image: none !important;
                }
                #nav-sidebar .addlink:after { content: "" !important; }
                .object-tools a {
                    background-color: #003459 !important;
                    color: #ffffff !important;
                    border-radius: 50px !important;
                    padding: 6px 15px !important;
                    text-transform: uppercase;
                    font-size: 11px;
                    font-weight: bold;
                }
                .object-tools a:hover { background-color: #00A8E8 !important; }
                .field-sync_button a.button, a.button {
                    background: var(--azure) !important;
                    color: var(--dark-navy) !important;
                    border-radius: 4px !important;
                    padding: 4px 12px !important;
                }
                #nav-sidebar tr.current-model { background: var(--azure) !important; }
                #nav-sidebar tr.current-model th a { color: var(--dark-navy) !important; }
                #nav-sidebar .model-taskresult .addlink,
                #nav-sidebar .model-groupresult .addlink { display: none !important; }
                .app-django_celery_results .object-tools { display: none !important; }
            </style>
        """)
        return context

    def get_app_list(self, request, app_label=None):
        """Custom menu grouping."""
        app_dict = self._build_app_dict(request)
        if not app_dict:
            return []

        all_models = []
        for app in app_dict.values(): 
            all_models.extend(app['models'])
            
        def find_model(name): return next((m for m in all_models if m['object_name'].lower() == name.lower()), None)
        
        inventory = {'name': 'Inventory', 'models': [m for m in [find_model('ESLTag'), find_model('Product')] if m]}
        hardware = {'name': 'Hardware', 'models': [m for m in [find_model('Gateway'), find_model('TagHardware')] if m]}
        org = {'name': 'Organisation', 'models': [m for m in [find_model('Company'), find_model('Store'), find_model('User'),find_model('Group')] if m]}
        
        # System Monitoring - Visible to Superusers, Owners, and Managers
        monitoring = {'name': 'System Monitoring', 'models': []}
        if request.user.is_superuser or request.user.role in ['owner', 'manager']:
            # Celery results registration
            celery_res = find_model('TaskResult')
            if celery_res: monitoring['models'].append(celery_res)
            
        groups = [inventory, hardware, org]
        if monitoring['models']: groups.append(monitoring)
        
        return groups


admin_site = SAISAdminSite(name='sais_admin')


# =================================================================
# MIXINS
# =================================================================

class AuditAdminMixin:
    """Automatically stamps the user who last modified the record."""
    
    def save_model(self, request, obj, form, change):
        if hasattr(obj, 'updated_by'):
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)


class CompanySecurityMixin(AuditAdminMixin):
    """Filters querysets based on company and role."""
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs

        if hasattr(self.model, 'company'):
            qs = qs.filter(company=request.user.company)
        elif hasattr(self.model, 'store'):
            qs = qs.filter(store__company=request.user.company)
        elif self.model == ESLTag:
            qs = qs.filter(gateway__store__company=request.user.company)

        if request.user.role == 'manager':
            assigned_stores = request.user.managed_stores.all()
            if hasattr(self.model, 'store'):
                qs = qs.filter(store__in=assigned_stores)
            elif self.model == Store:
                qs = qs.filter(id__in=assigned_stores.values_list('id', flat=True))
            elif self.model == ESLTag:
                qs = qs.filter(gateway__store__in=assigned_stores)

        return qs


class StoreFilteredAdmin(admin.ModelAdmin):
    """Base for store-specific models with list and dropdown filtering."""

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        active_store = getattr(request, 'active_store', None)
        
        if active_store:
            if self.model == Product:
                return qs.filter(store=active_store)
            if self.model == ESLTag:
                return qs.filter(gateway__store=active_store)
            if self.model == Gateway:
                return qs.filter(store=active_store)
        
        if request.user.is_superuser:
            return qs
            
        return qs.none()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Filter dropdowns to active store."""
        if not request.user.is_superuser and hasattr(request, 'active_store'):
            if db_field.name == "gateway":
                kwargs["queryset"] = Gateway.objects.filter(store=request.active_store)
            if db_field.name == "paired_product":
                kwargs["queryset"] = Product.objects.filter(store=request.active_store)
            if db_field.name == "store":
                kwargs["queryset"] = Store.objects.filter(id=request.active_store.id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class UIHelperMixin:
    """Shared display methods for cleaner list views."""
    
    def sync_button(self, obj):
        url = reverse('admin:sync-tag-manual', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}" style="background:#2563eb; color:white;">Sync</a>', 
            url
        )
    sync_button.short_description = "Action"


# class BulkDeleteDisabledMixin:
#     """
#     Mixin to disable bulk delete action and replace with safer alternatives.
#     Prevents accidental deletion of all items.
#     """
    
#     def get_actions(self, request):
#         actions = super().get_actions(request)
#         # Remove the default delete_selected action
#         if 'delete_selected' in actions:
#             del actions['delete_selected']
#         return actions
    
#     def has_delete_permission(self, request, obj=None):
#         """Allow single item deletion only."""
#         # If obj is None, this is for bulk delete which we want to prevent
#         if obj is None and request.method == 'POST':
#             return False
#         return super().has_delete_permission(request, obj)


# =================================================================
# COMPANY ADMIN
# =================================================================

@admin.register(Company, site=admin_site)
class CompanyAdmin(CompanySecurityMixin, admin.ModelAdmin):
    list_display = ('name', 'contact_email', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('contact_email', 'is_active')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(id=request.user.company_id)

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


# =================================================================
# STORE ADMIN
# =================================================================

@admin.register(Store, site=admin_site)
class StoreAdmin(CompanySecurityMixin, admin.ModelAdmin):
    list_display = ('name', 'company', 'location_code', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('location_code', 'is_active')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')
    
    def get_readonly_fields(self, request, obj=None):
        if not request.user.is_superuser:
            return ('company',)
        return ()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "company" and not request.user.is_superuser:
            kwargs["queryset"] = Company.objects.filter(id=request.user.company_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# =================================================================
# GATEWAY ADMIN
# =================================================================

@admin.register(Gateway, site=admin_site)
class GatewayAdmin(CompanySecurityMixin, UIHelperMixin, StoreFilteredAdmin):
    list_display = ('estation_id','gateway_mac', 'store', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('store', 'is_active')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')

    def get_readonly_fields(self, request, obj=None):
        if not request.user.is_superuser:
            return self.readonly_fields + ('store',)
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        if not change and not request.user.is_superuser:
            if hasattr(request, 'active_store'):
                obj.store = request.active_store
        super().save_model(request, obj, form, change)


# =================================================================
# TAG HARDWARE ADMIN
# =================================================================

@admin.register(TagHardware, site=admin_site)
class TagHardwareAdmin(admin.ModelAdmin):
    list_display = ('model_number', 'width_px', 'height_px', 'color_scheme', 'display_size_inch', 'created_at', 'updated_at', 'updated_by')
    readonly_fields = ('updated_at', 'updated_by')
    
    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser


# =================================================================
# PRODUCT ADMIN
# =================================================================

@admin.register(Product, site=admin_site)
class ProductAdmin( CompanySecurityMixin, UIHelperMixin, StoreFilteredAdmin):
    list_display = ('image_status', 'sku', 'name', 'price', 'store', 'sync_button', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('name', 'price')
    search_fields = ('sku', 'name')
    readonly_fields = ('updated_at', 'updated_by', 'image_status')
    change_list_template = "admin/core/product/change_list.html"

# This prevents the "Show All" link at the bottom from appearing for large sets
    list_max_show_all = 100
    show_full_result_count = False
    
    def get_actions(self, request):
        actions = super().get_actions(request)
        # We delete the default 'delete_selected' because it's dangerous
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    # Your custom, safe actions
    actions = ['safe_delete', 'regenerate_product_images', 'refresh_all_store_images']

    @admin.action(description="Delete selected (Max 100)")
    def safe_delete(self, request, queryset):
        # Double-check the count just in case
        if queryset.count() > 100:
            self.message_user(request, "Error: You cannot delete more than 100 items.", messages.ERROR)
            return
        queryset.delete()

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if not request.user.is_superuser:
            return fields + ('store',)
        return fields


    @admin.action(description="Regenerate Tag Images for selected products")
    def regenerate_product_images(self, request, queryset):
        from core.utils import trigger_bulk_sync
        
        # Limit selection
        # max_items = getattr(settings, 'BULK_OPERATION_LIMIT', 100)
        # if queryset.count() > max_items:
        #     self.message_user(
        #         request, 
        #         f"Please select no more than {max_items} products at once.",
        #         messages.ERROR
        #     )
        #     return
        if queryset.count() > 100:
            self.message_user(request, "Error: Please select maximum 100 items.", messages.ERROR)
            return
        queryset.delete()
        
        tag_ids = list(ESLTag.objects.filter(
            paired_product__in=queryset
        ).values_list('id', flat=True))
        
        if tag_ids:
            group_result = trigger_bulk_sync(tag_ids)
            if group_result:
                self.message_user(
                    request, 
                    f"Queued {len(tag_ids)} tag updates across selected products."
                )
        else:
            self.message_user(request, "No tags found for selected products.", messages.WARNING)

    @admin.action(description="Refresh ALL images for this Store")
    def refresh_all_store_images(self, request, queryset):
        # We don't use 'queryset' (the checkboxes). 
        # We use the active store from your middleware.
        if not request.active_store:
            self.message_user(request, "Please select a store first.", messages.WARNING)
            return

        # Trigger a single background task for the whole store
        from core.tasks import refresh_store_products_task
        refresh_store_products_task.delay(request.active_store.id)
        
        self.message_user(request, f"Task started: Refreshing all products for {request.active_store.name}")

    # @admin.action(description="🔄 Refresh All Tags (Regenerate images for all products with matching tags)")
    # def refresh_all_matched_tags(self, request, queryset):
    #     """
    #     Refresh all tags that are paired with products in the current store.
    #     This is a safer alternative to bulk delete - regenerates images instead.
    #     """
    #     from core.utils import trigger_bulk_sync
        
    #     active_store = getattr(request, 'active_store', None)
    #     if not active_store:
    #         self.message_user(request, "Please select a store first.", messages.ERROR)
    #         return
        
    #     # Get all tags in the store that have paired products
    #     tag_ids = list(ESLTag.objects.filter(
    #         gateway__store=active_store,
    #         paired_product__isnull=False,
    #         hardware_spec__isnull=False
    #     ).values_list('id', flat=True))
        
    #     if not tag_ids:
    #         self.message_user(request, "No paired tags found in this store.", messages.WARNING)
    #         return
        
    #     # Process in batches
    #     max_items = getattr(settings, 'BULK_OPERATION_LIMIT', 100)
    #     if len(tag_ids) > max_items:
    #         tag_ids = tag_ids[:max_items]
    #         self.message_user(
    #             request,
    #             f"Processing first {max_items} tags. Run again for remaining tags.",
    #             messages.WARNING
    #         )
        
    #     group_result = trigger_bulk_sync(tag_ids)
    #     if group_result:
    #         self.message_user(
    #             request,
    #             f"Queued {len(tag_ids)} tags for image regeneration in background."
    #         )

    def image_status(self, obj):
        has_image = obj.esl_tags.filter(tag_image__gt='').exists()
        has_tag = obj.esl_tags.exists()
        
        if has_image:
            return mark_safe('<span style="color: #059669; font-weight: bold;">● Generated</span>')
        if has_tag:
            return mark_safe('<span style="color: #ea580c; font-weight: bold;">● Pending</span>')
        return mark_safe('<span style="color: #94a3b8;">○ No Tag</span>')
    image_status.short_description = "Status"
    image_status.admin_order_field = 'has_tag_image'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            has_tag_image=Count('esl_tags', filter=Q(esl_tags__tag_image__gt=''))
        )

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        if not change and hasattr(request, 'active_store'):
            obj.store = request.active_store
        super().save_model(request, obj, form, change)

    def get_urls(self):
        return [
            path('import-modisoft/', self.admin_site.admin_view(preview_product_import), name='import-modisoft')
        ] + super().get_urls()


# =================================================================
# ESL TAG ADMIN
# =================================================================

@admin.register(ESLTag, site=admin_site)
class ESLTagAdmin( CompanySecurityMixin, UIHelperMixin, StoreFilteredAdmin):
    change_list_template = "admin/core/esltag/change_list.html"
    list_display = ('image_status', 'tag_mac', 'get_paired_info', 'sync_state', 'battery_status', 'hardware_spec', 'gateway',
                   'sync_button', 'aisle', 'section', 'shelf_row', 'updated_at', 'created_at', 'updated_by')
    
    list_display = (
        'image_status', 
        'tag_mac', 
        'get_paired_info', 
        'last_sync_status', 
        'battery_level_display', # This replaces battery_status
        'hardware_spec', 
        'gateway',
        'sync_button', 
        'aisle', 
        'section', 
        'shelf_row',
        'updated_at', 
        'created_at', 
        'updated_by',
    )
    
   # list_editable = ('aisle', 'section', 'shelf_row')
    list_filter = ('sync_state', 'gateway__store', 'hardware_spec')
    autocomplete_fields = ['paired_product']
    readonly_fields = ('get_paired_info', 'image_preview_large', 'sync_state','last_image_gen_success', 'last_image_task_id', 'audit_log_link','updated_at', 'updated_by', 'created_at')
#    actions = ['regenerate_tag_images', 'refresh_all_store_tags']
    actions = ['safe_delete','safe_regenerate_images', 'refresh_all_store_tags']
    show_full_result_count = False
    list_max_show_all = 100
    
    fieldsets = (
        ('Hardware', {'fields': ('tag_mac', 'gateway', 'hardware_spec', 'battery_level')}),
        ('Pairing', {
            'description': 'Search for a product by SKU or Name below.',
            'fields': ('paired_product',),
        }),
        ('Visuals', {'fields': ('image_preview_large','last_image_gen_success', 'sync_state','last_image_task_id', 'audit_log_link')}),
        ('Location', {'fields': ('aisle', 'section', 'shelf_row')}),
        ('Audit', {'fields': ('updated_by', 'updated_at')}),
    )

    def get_queryset(self, request):
        """
        Custom Queryset to handle Store Filtering and Optimization.
        """
        qs = super().get_queryset(request).select_related('gateway__store', 'paired_product')
        if hasattr(request, 'active_store') and request.active_store:
            return qs.filter(gateway__store=request.active_store)
        return qs

    # 2. SORTABLE Image Status
    def image_status(self, obj):
        if not obj.paired_product:
            return mark_safe('<span style="color:#94a3b8;">○ No Product</span>')
        color = "#059669" if obj.tag_image else "#ea580c"
        label = "Generated" if obj.tag_image else "Pending"
        return mark_safe(f'<span style="color:{color}; font-weight:bold;">● {label}</span>')
    image_status.short_description = "Image"
    image_status.admin_order_field = 'tag_image' # Sort by whether image exists

    def save_model(self, request, obj, form, change):
        if not change:
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    # 3. SORTABLE Paired Info
    def get_paired_info(self, obj):
        if obj.paired_product:
            return f"{obj.paired_product.sku} - {obj.paired_product.name}"
        return mark_safe('<i style="color: #94a3b8;">Unpaired</i>')
    get_paired_info.short_description = "Paired Product"
    get_paired_info.admin_order_field = 'paired_product__name' # Sort by product name

    def last_sync_status(self, obj):
        color_map = {
            'SUCCESS': '#059669',
            'PROCESSING': '#2563eb',
            'PUSHED': '#7c3aed',
            'IDLE': '#a2a2a3',
            'GEN_FAILED': '#f50000',
            'PUSH_FAILED': '#f50000',
            'IMAGE_READY': '#f5ac00',
            'FAILED': '#f50000'
        }

        color = color_map.get(obj.sync_state, '#ea580c')
        status_text = obj.get_sync_state_display()
        if obj.last_image_gen_success and obj.sync_state == 'SUCCESS':
            status_text = f"✔ {obj.last_image_gen_success.strftime('%H:%M')}"
        return format_html('<span style="color: {}; font-weight: bold;">{}</span>', color, status_text)
    last_sync_status.short_description = "Sync Status"
    last_sync_status.admin_order_field = 'sync_state'

    # 4. SORTABLE Battery Display
    def battery_level_display(self, obj):
        val = obj.battery_level or 0
        color = "#059669" if val > 20 else "#dc2626"
        return format_html(
            '<div style="width: 80px; background: #eee; border-radius: 3px; height: 10px; display: inline-block; margin-right: 5px;">'
            '<div style="width: {}%; background: {}; height: 10px; border-radius: 3px;"></div>'
            '</div><small>{}%</small>',
            val, color, val
        )
    battery_level_display.short_description = "Battery"
    battery_level_display.admin_order_field = 'battery_level'

    def sync_button(self, obj):
        url = reverse('admin:sync-tag-manual', args=[obj.pk])
        return format_html('<a style="background:#2563eb; color:white; padding:2px 8px; border-radius:4px; font-size:10px; text-decoration:none;" href="{}">SYNC</a>', url)
    sync_button.short_description = "Action"

    def audit_log_link(self, obj):
        if not obj.last_image_task_id:
            return "No task record"
        try:
            url = reverse('admin:django_celery_results_taskresult_changelist') + f"?task_id={obj.last_image_task_id}"
            return format_html('<a href="{}" target="_blank">View Task Results ↗</a>', url)
        except:
            return obj.last_image_task_id
    audit_log_link.short_description = "Audit Trail"

    def image_preview_large(self, obj):
        if not obj.paired_product:
            return mark_safe('<i style="color: #94a3b8;">No product paired.</i>')
        if obj.tag_image:
            return format_html(
                '<img src="{}?v={}" style="max-width: 400px; border: 2px solid #eee; border-radius: 12px;"/>', 
                obj.tag_image.url, int(time.time())
            )
        return "Waiting for background generation..."
    image_preview_large.short_description = "Current Tag Image"

    # --- ACTIONS ---
    @admin.action(description="Regenerate selected (Max 100)")
    def safe_regenerate_images(self, request, queryset):
        count = queryset.count()
        if count > 100:
            self.message_user(request, "Error: Max 100 tags allowed.", messages.ERROR)
            return
        for tag in queryset:
            update_tag_image_task.delay(tag.id)
        self.message_user(request, f"Queued {count} tags for image regeneration.")

    @admin.action(description="Delete selected (Max 100)")
    def safe_delete(self, request, queryset):
        if queryset.count() > 100:
            self.message_user(request, "Error: Max 100 items allowed.", messages.ERROR)
            return
        queryset.delete()

    @admin.action(description="Refresh ALL tags in Store")
    def refresh_all_store_tags(self, request, queryset):
        active_store = getattr(request, 'active_store', None)
        if not active_store:
            self.message_user(request, "Please select a store first.", messages.WARNING)
            return
        # Filter for tags in this store with product pairing
        tags = ESLTag.objects.filter(gateway__store=active_store, paired_product__isnull=False)
        count = tags.count()
        for tag in tags[:200]: # Safety limit for bulk refresh
            update_tag_image_task.delay(tag.id)
        self.message_user(request, f"Queued refresh for {min(count, 200)} tags in {active_store.name}.")

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def manual_sync_view(self, request, object_id):
        update_tag_image_task.delay(object_id)
        messages.success(request, "Sync task queued.")
        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))



    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('download-template/', self.admin_site.admin_view(download_tag_template), name='download_tag_template'),
            path('bulk-map/', self.admin_site.admin_view(bulk_map_tags_view), name='bulk-map-tags'),
            path('import-preview/', self.admin_site.admin_view(preview_tag_import), name='preview_tag_import'),
            path('<path:object_id>/sync/', self.admin_site.admin_view(self.manual_sync_view), name='sync-tag-manual'),
        ]
        return custom_urls + urls
        
    #----

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['custom_css'] = mark_safe("""
            <style>
                .column-image_status { width: 100px !important; min-width: 100px !important; }
                .column-get_paired_info { width: 450px !important; min-width: 300px !important; }
                .field-get_paired_info { white-space: nowrap !important; overflow: hidden; text-overflow: ellipsis; }
                .column-hardware_spec { width: 130px !important; min-width: 130px !important; }
                .column-aisle { width: 20px !important; min-width: 20px !important; }
                .column-section { width: 80px !important; min-width: 80px !important; }
                .column-shelf_row { width: 80px !important; text-align: center !important; min-width: 80px !important; }
                .column-image_status { width: 100px !important; text-align: center !important; }
            </style>
        """)
        return super().changelist_view(request, extra_context=extra_context)



# =================================================================
# USER ADMIN
# =================================================================

@admin.register(User, site=admin_site)
class CustomUserAdmin(UserAdmin, CompanySecurityMixin):
    list_display = ('username', 'company', 'role', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (
        ('Store Allocation', {'fields': ('managed_stores', 'company', 'role')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Store Allocation', {'fields': ('managed_stores', 'company', 'role')}),
    )
    filter_horizontal = ('managed_stores',)

    def get_readonly_fields(self, request, obj=None):
        if not request.user.is_superuser:
            return self.readonly_fields + ('company',)
        return self.readonly_fields

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        for i, (name, field_options) in enumerate(fieldsets):
            if name == 'Permissions':
                fields = list(field_options.get('fields', []))
                for f in ['groups', 'user_permissions', 'is_superuser']:
                    if f in fields:
                        fields.remove(f)
                fieldsets[i] = (name, {**field_options, 'fields': tuple(fields)})
        return fieldsets

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "company" and not request.user.is_superuser:
            kwargs["queryset"] = Company.objects.filter(id=request.user.company_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        
        qs = qs.filter(company=request.user.company)

        if request.user.role == 'owner':
            return qs.exclude(is_superuser=True)
        
        if request.user.role == 'manager':
            return qs.filter(
                Q(role__in=['manager', 'readonly']) & 
                Q(managed_stores__in=request.user.managed_stores.all())
            ).distinct()

        return qs.filter(id=request.user.id)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            if db_field.name == "managed_stores":
                if request.user.role == 'owner':
                    kwargs["queryset"] = Store.objects.filter(company=request.user.company)
                else:
                    kwargs["queryset"] = request.user.managed_stores.all()
            
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
            
            # Prevent privilege escalation
            if obj.role == 'owner' and request.user.role != 'owner':
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("You cannot assign a role higher than your own.")
        
        super().save_model(request, obj, form, change)
        
        # Automated group assignment
        role_map = {'owner': 'Owner', 'manager': 'Store Manager', 'readonly': 'Read Only'}
        group_name = role_map.get(obj.role, 'Store Staff')
        group, _ = Group.objects.get_or_create(name=group_name)
        obj.groups.clear()
        obj.groups.add(group)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        
        if not request.user.is_superuser:
            current_role = getattr(request.user, 'role', 'staff')
            
            allowed_roles = []
            if current_role == 'owner':
                allowed_roles = [('manager', 'Store Manager'), ('staff', 'Store Staff'), ('readonly', 'Read Only')]
            elif current_role == 'manager':
                allowed_roles = [('staff', 'Store Staff'), ('readonly', 'Read Only')]
            else:
                allowed_roles = [('readonly', 'Read Only')]
            
            if 'role' in form.base_fields:
                form.base_fields['role'].choices = allowed_roles
                
        return form


# =================================================================
# GROUP RESULT ADMIN
# =================================================================

try:
    admin.site.unregister(GroupResult)
except admin.sites.NotRegistered:
    pass


@admin.register(GroupResult)
class CustomGroupResultAdmin(GroupResultAdmin):
    list_display = ('group_id', 'batch_progress', 'date_done')
    readonly_fields = ('group_id', 'date_done', 'failure_details')

    def batch_progress(self, obj):
        try:
            task_ids = json.loads(obj.result) if isinstance(obj.result, str) else obj.result
            if not task_ids:
                return "0 Tasks"
            
            total = len(task_ids)
            queryset = TaskResult.objects.filter(task_id__in=task_ids)
            completed = queryset.filter(status='SUCCESS').count()
            failed = queryset.filter(status='FAILURE').count()
            percent = int(((completed + failed) / total) * 100)
            
            return format_html(
                "<b>{}%</b> (✅ {} | ❌ {} | Total {})",
                percent, completed, failed, total
            )
        except Exception:
            return "Pending/Invalid Data"
    batch_progress.short_description = "Batch Progress"

    def failure_details(self, obj):
        try:
            task_ids = json.loads(obj.result) if isinstance(obj.result, str) else obj.result
            failed_tasks = TaskResult.objects.filter(task_id__in=task_ids).exclude(status='SUCCESS')
            
            if not failed_tasks.exists():
                return "All tags in this batch processed successfully."

            rows = "".join([
                f"<tr><td style='padding:5px; border-bottom:1px solid #eee;'>{t.task_id}</td>"
                f"<td style='padding:5px; border-bottom:1px solid #eee;'>{t.status}</td>"
                f"<td style='padding:5px; border-bottom:1px solid #eee;'>{t.result}</td></tr>" 
                for t in failed_tasks
            ])
            
            return mark_safe(f"""
            <table style='width:100%; border-collapse: collapse; text-align: left;'>
                <thead>
                    <tr style='background: #f8f8f8;'>
                        <th>Task ID</th><th>Status</th><th>Result/Error</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
            """)
        except Exception as e:
            return f"Error parsing failures: {str(e)}"
    failure_details.short_description = "Failure Report"


# =================================================================
# REGISTER ADDITIONAL MODELS
# =================================================================

admin_site.register(Group)

try:
    admin_site.register(TaskResult, TaskResultAdmin)
    admin_site.register(GroupResult, GroupResultAdmin)
except admin.sites.AlreadyRegistered:
    pass
