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
import time
from django_celery_results.models import TaskResult, GroupResult
from django_celery_results.admin import GroupResultAdmin
import json


# =================================================================
# 1. MIXINS & SECURITY
# =================================================================
class SAISAdminSite(admin.AdminSite):

    site_header = "SAIS Platform Administration"
    site_title = "SAIS Admin"
    index_title = "Welcome to SAIS Control Panel"
    
    def each_context(self, request):
        context = super().each_context(request)
    
        # Unique version ID for this page load to break browser cache
        v = int(time.time())
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

                /* 1. BRANDING (Fixes circled logo color) */
                #header { background: var(--navy); border-bottom: 3px solid var(--light-gray); }
                #branding h1 a { color: var(--white) !important; }

                /* 2. SIDEBAR HEADERS (Fixes full-width dark navy background) */
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
                /* Header Icon for System Monitoring */
                .app-monitoring .section:before { content: "⚙️"; margin-right: 10px; }

                /* 3. MENU ITEMS & ICONS */
                #nav-sidebar th a { color: var(--navy) !important; }
                #nav-sidebar .model-esltag th a:before { content: "🏷️ "; }
                #nav-sidebar .model-product th a:before { content: "🛒 "; }
                #nav-sidebar .model-gateway th a:before { content: "📟 "; }
                #nav-sidebar .model-taghardware th a:before { content: "🛠️ "; }
                #nav-sidebar .model-company th a:before { content: "🏭 "; }
                #nav-sidebar .model-store th a:before { content: "🏪 "; }
                #nav-sidebar .model-user th a:before { content: "👤 "; }
                #nav-sidebar .model-group th a:before { content: "👥 "; }
                /* Icon for Task Results model */
                #nav-sidebar .model-taskresult th a:before { content: "📊 "; }
                #nav-sidebar .model-groupresult th a:before { content: "📁 "; }

                /* 4. FIX DOUBLE "ADD" & SIDEBAR BUTTONS */
                #nav-sidebar .addlink {
                    background: var(--navy) !important;
                    color: var(--white) !important;
                    padding: 3px 8px !important;
                    border-radius: 4px;
                    text-transform: uppercase;
                    font-size: 10px;
                    background-image: none !important; /* Removes default plus icon */
                }
                /* Removes the manual text we added that caused the double 'ADD' */
                #nav-sidebar .addlink:after { content: "" !important; }

                /* 5. TOP ACTION BUTTONS (Download/Import/Add ESL Tag) */
                .object-tools a {
                    background-color: #003459 !important;
                    color: #ffffff !important;
                    border-radius: 50px !important; /* Creates the pill effect */
                    padding: 6px 15px !important;
                    text-transform: uppercase;
                    font-size: 11px;
                    font-weight: bold;
                }
                .object-tools a:hover {
                    background-color: #00A8E8 !important;
                }
                /* 6. SYNC BUTTON IN TABLE */
                .field-sync_button a.button, a.button {
                    background: var(--azure) !important;
                    color: var(--dark-navy) !important;
                    border-radius: 4px !important;
                    padding: 4px 12px !important;
                }

                /* 7. SELECTED STATE */
                #nav-sidebar tr.current-model { background: var(--azure) !important; }
                #nav-sidebar tr.current-model th a { color: var(--dark-navy) !important; }

                /* Hide the 'Add' buttons for Task and Group results in the sidebar */
                #nav-sidebar .model-taskresult .addlink,
                #nav-sidebar .model-groupresult .addlink {
                    display: none !important;
                }

                /* Hide the 'Add' button on the actual list pages */
                .app-django_celery_results .object-tools {
                    display: none !important;
                }
            </style>
        """)
        return context


    def get_app_list(self, request, app_label=None):
        """
        Customizes the main dashboard menu to group models into Inventory, 
        Hardware, and Organisation.
        """
        app_dict = self._build_app_dict(request)
        if not app_dict:
            return []

        # Extract the models from your 'core' app (and 'auth' for users/groups)
        # Note: Adjust the 'core' label if your app name is different
        all_models = []
        for app in app_dict.values():
            all_models.extend(app['models'])

        # Create a helper function to find a model by its name
        def find_model(name):
            return next((m for m in all_models if m['object_name'].lower() == name.lower()), None)

        # Define your custom groups
        custom_groups = [
            {
                'name': 'Inventory',
                'app_label': 'inventory',
                'models': [m for m in [find_model('ESLTag'), find_model('Product')] if m],
            },
            {
                'name': 'Hardware',
                'app_label': 'hardware',
                'models': [m for m in [find_model('Gateway'), find_model('TagHardware')] if m],
            },
            {
                'name': 'Organisation',
                'app_label': 'organisation',
                'models': [m for m in [find_model('Company'), find_model('Store'), 
                                     find_model('User'), find_model('Group')] if m],
            },
            {
                'name': 'System Monitoring',
                'app_label': 'monitoring',
                'models': [m for m in [find_model('TaskResult'), find_model('GroupResult')] if m],
            },
        ]
        return custom_groups

# Instantiate the custom admin site
admin_site = SAISAdminSite(name='sais_admin')

class AuditAdminMixin:
    """Automatically stamps the user who last modified the record."""
    def save_model(self, request, obj, form, change): #AuditAdminMixin
        if hasattr(obj, 'updated_by'):
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)

#---looks like this is not in use.
#@admin.action(description='Regenerate images in background')
#def regenerate_product_tags(modeladmin, request, queryset):
#    count = 0
#    for item in queryset:
#        if isinstance(item, Product):
#            for tag in item.esl_tags.all():
#                update_tag_image_task.delay(tag.id)
#                count += 1
#        elif isinstance(item, ESLTag):
#            update_tag_image_task.delay(item.id)
#            count += 1
#    modeladmin.message_user(request, f"Queued {count} tasks for background image processing.")


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
@admin.register(Company, site=admin_site)
class CompanyAdmin(CompanySecurityMixin, admin.ModelAdmin):
    list_display = ('name', 'contact_email', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('contact_email', 'is_active')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')


# =================================================================
# 2. STORE ADMIN
# =================================================================
@admin.register(Store, site=admin_site)
class StoreAdmin(CompanySecurityMixin, admin.ModelAdmin):
    list_display = ('name', 'company', 'location_code', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('location_code', 'is_active')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')
    def get_readonly_fields(self, request, obj=None):
        if not request.user.is_superuser:
            return ('company',) # Owners cannot change their company
        return ()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "company" and not request.user.is_superuser:
            # Filter to only show the user's own company
            kwargs["queryset"] = Company.objects.filter(id=request.user.company_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

# =================================================================
# 3. GATEWAY ADMIN
# =================================================================
@admin.register(Gateway, site=admin_site)
class GatewayAdmin(CompanySecurityMixin, admin.ModelAdmin):
    list_display = ('gateway_mac', 'store', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('store', 'is_active')
    readonly_fields = ( 'created_at', 'updated_at', 'updated_by')

# =================================================================
# 3. TAG HARDWARE ADMIN
# =================================================================
@admin.register(TagHardware, site=admin_site)
class TagHardwareAdmin(admin.ModelAdmin):
    list_display = ('model_number', 'width_px', 'height_px', 'color_scheme', 'display_size_inch', 'created_at', 'updated_at', 'updated_by')
    readonly_fields = ( 'updated_at', 'updated_by')
    def has_change_permission(self, request, obj=None): return request.user.is_superuser


# =================================================================
# 2. Product ADMIN
# =================================================================


@admin.register(Product, site=admin_site)
class ProductAdmin(CompanySecurityMixin, UIHelperMixin, admin.ModelAdmin):


    list_display = ('image_status', 'sku', 'name', 'price', 'store', 'sync_button', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('name', 'price')
    search_fields = ('sku', 'name')
    readonly_fields = ('updated_at', 'updated_by','image_status')

    change_list_template = "admin/core/product/change_list.html"
    actions = ['regenerate_product_images', 'sync_products']

# admin.py (inside ProductAdmin class)

    @admin.action(description="Regenerate Tag Images for selected products (Grouped)")
    def regenerate_product_images(self, request, queryset):
        from core.utils import trigger_bulk_sync
        from .models import ESLTag
        
        # Get all tags associated with these products
        tag_ids = list(ESLTag.objects.filter(
            paired_product__in=queryset
        ).values_list('id', flat=True))
        
        group_result = trigger_bulk_sync(tag_ids)
        
        if group_result:
            self.message_user(
                request, 
                f"Queued {len(tag_ids)} tag updates across selected products."
            )

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
    image_status.admin_order_field = 'has_tag_image' # Links sorting to annotated field

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            has_tag_image=Count('esl_tags', filter=Q(esl_tags__tag_image__gt=''))
        )

    def save_model(self, request, obj, form, change):
        # 1. Handle Audit (User tracking)
        obj.updated_by = request.user
        
        # 2. Handle Store Context (Assign store if it's a new product)
        if not change and hasattr(request, 'active_store'):
            obj.store = request.active_store
        
        # 3. Save the object
        # This triggers the 'post_save' signal in signals.py 
        # which now handles the background tasks correctly.
        super().save_model(request, obj, form, change)

        # 4. REMOVE the loop calling update_tag_image_task.delay(tag.id)
        # The signal in signals.py is the cleaner place for this!

    def get_urls(self):
        return [path('import-modisoft/', self.admin_site.admin_view(preview_product_import), name='import-modisoft')] + super().get_urls()

# =================================================================
# 3. ESL TAG ADMIN
# =================================================================

@admin.register(ESLTag, site=admin_site)
class ESLTagAdmin(CompanySecurityMixin, UIHelperMixin, admin.ModelAdmin):  
    # Fixed UI Widths via CSS injection
    change_list_template = "admin/core/esltag/change_list.html" 
    list_display = ('image_status', 'tag_mac', 'get_paired_info', 'battery_status', 'hardware_spec','sync_button', 'aisle', 'section', 'shelf_row', 'updated_at', 'created_at', 'updated_by')
    list_editable = (  'aisle', 'section', 'shelf_row')

    # 2. Restored AJAX Autocomplete (Search as you type)
    autocomplete_fields = ['paired_product']
    
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
            # Adding ?v= plus a timestamp forces the browser to bypass its cache
            return format_html('<img src="{}?v={}" style="width: 50px; height: auto; border-radius: 4px;"/>', 
                               obj.tag_image.url, int(time.time()))
        return "-"

    def battery_status(self, obj):
        val = obj.battery_level
        color = "#059669" if val > 50 else "#ea580c" if val > 20 else "#dc2626"
        return format_html('<b style="color: {};">{}%</b>', color, val)
    battery_status.short_description = "Battery"

    def image_preview_large(self, obj):
        # 1. Check if a product is even paired
        if not obj.paired_product:
            return mark_safe('<i style="color: #94a3b8;">No product paired - cannot generate image.</i>')
            
        # 2. If paired, check if the image exists
        if obj.tag_image:
            # The ?v= timestamp ensures you see the latest version without Shift+Refresh
            return format_html(
                '<img src="{}?v={}" style="max-width: 400px; border: 2px solid #eee; border-radius: 12px;"/>', 
                obj.tag_image.url, 
                int(time.time())
            )
            
        # 3. If paired but no image file yet
        return "Waiting for background generation..."
    
    image_preview_large.short_description = "Current Tag Image"

    def get_product_name(self, obj):
        return f"{obj.paired_product.name} ({obj.paired_product.sku})" if obj.paired_product else "-"
    get_product_name.short_description = 'Paired Product'

    def tag_image_thumbnail(self, obj):
        if obj.tag_image:
            return format_html('<img src="{}?v={}" style="width: 50px; height: auto;" />', 
                               obj.tag_image.url, int(time.time()))
        return "No Image"
    
    def tag_image_preview(self, obj):
        if obj.tag_image:
            return format_html('<img src="{}" style="max-width: 300px; height: auto;" />', obj.tag_image.url)
        return "No Image Preview Available"

# Point 7: Zero-File CSS Injection for Widths
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['custom_css'] = mark_safe("""
            <style>
                .column-image_status { 
                    width: 100px !important; 
                    min-width: 100px !important; 
                }

                /* Target the exact class name found via Inspect */
                .column-get_paired_info { 
                    width: 450px !important; 
                    min-width: 300px !important; 
                }

                /* Ensure the text doesn't wrap into a tiny vertical column */
                .field-get_paired_info {
                    white-space: nowrap !important;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }
                /* Target the exact class name found via Inspect */
                .column-hardware_spec { 
                    width: 130px !important; 
                    min-width: 130px !important; 
                }

                /* Target the exact class name found via Inspect */
                .column-aisle { 
                    width: 20px !important; 
                    min-width: 20px !important; 
                }
                /* Target the exact class name found via Inspect */
                .column-section,{ 
                    width: 80px !important; 
                    min-width: 80px !important; 
                }
                /* Shrink and center-align the location columns */
                .column-shelf_row { 
                    width: 80px !important; 
                    text-align: center !important; 
                    min-width: 80px !important;
                }

                /* Force the image status column to be narrow */
                .column-image_status { 
                    width: 100px !important; 
                    text-align: center !important;
                }
            </style>
        """)
        return super().changelist_view(request, extra_context=extra_context)

    @admin.action(description="Regenerate Images for selected tags (Grouped)")
    def regenerate_tag_images(self, request, queryset):
        from core.utils import trigger_bulk_sync # Local import to avoid circularity
        
        tag_ids = list(queryset.values_list('id', flat=True))
        group_result = trigger_bulk_sync(tag_ids)
        
        if group_result:
            self.message_user(
                request, 
                f"Queued bulk update for {len(tag_ids)} tags. Group ID: {group_result.id}"
            )

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

    def save_model(self, request, obj, form, change):
    # Save the object first
        super().save_model(request, obj, form, change)
    
    # Trigger the image update task for this specific tag
    # We use .delay() for individual saves
        update_tag_image_task(obj.id)

    def get_urls(self):
        urls = super().get_urls()
        # Custom URLs must come BEFORE the standard change_view URL to avoid ID conflict
        custom_urls = [
            path('download-template/', self.admin_site.admin_view(download_tag_template), name='download_tag_template'),
            path('import-preview/', self.admin_site.admin_view(preview_tag_import), name='preview_tag_import'),
            path('<path:object_id>/sync/', self.admin_site.admin_view(self.manual_sync_view), name='sync-tag-manual'),
        ]
        return custom_urls + urls


# =================================================================
# 4. SYSTEM & USER ADMIN
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

    def save_model(self, request, obj, form, change):#custom user save_model
        if not request.user.is_superuser:
            obj.company = request.user.company
        super().save_model(request, obj, form, change)
        
        role_map = {'owner': 'Owner', 'manager': 'Store Manager', 'readonly': 'Read Only'}
        group_name = role_map.get(obj.role, 'Store Staff')
        group, _ = Group.objects.get_or_create(name=group_name)
        obj.groups.add(group)
#------------------------------------------------------------------
#--------------------------GROUP RESULT ADMIN CUSTOMIZATION--------------------------
#-------------------------------------------------------------------

# Unregister if already registered to avoid admin.E002
try:
    admin.site.unregister(GroupResult)
except admin.sites.NotRegistered:
    pass

@admin.register(GroupResult)
class CustomGroupResultAdmin(GroupResultAdmin):
    # Match the function name exactly here
    list_display = ('group_id', 'batch_progress', 'date_done')
    readonly_fields = ('group_id', 'date_done', 'failure_details')

    def batch_progress(self, obj):
        """
        Calculates progress based ONLY on tasks associated with this specific group.
        """
        try:
            # Handle both string (JSON) and already-parsed list formats
            task_ids = json.loads(obj.result) if isinstance(obj.result, str) else obj.result
            if not task_ids:
                return "0 Tasks"
            
            total = len(task_ids)
            
            # Filter TaskResults that match THIS group's IDs only
            # This is what fixes your "218" global count issue
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
        """
        Displays a summary table of failed tasks in the detailed view.
        """
        try:
            task_ids = json.loads(obj.result) if isinstance(obj.result, str) else obj.result
            failed_tasks = TaskResult.objects.filter(task_id__in=task_ids).exclude(status='SUCCESS')
            
            if not failed_tasks.exists():
                return "All tags in this batch processed successfully."

            # Styling the table for the Django Admin UI
            rows = "".join([
                f"<tr><td style='padding:5px; border-bottom:1px solid #eee;'>{t.task_id}</td>"
                f"<td style='padding:5px; border-bottom:1px solid #eee;'>{t.status}</td>"
                f"<td style='padding:5px; border-bottom:1px solid #eee;'>{t.result}</td></tr>" 
                for t in failed_tasks
            ])
            
            html = f"""
            <table style='width:100%; border-collapse: collapse; text-align: left;'>
                <thead>
                    <tr style='background: #f8f8f8;'>
                        <th>Task ID</th><th>Status</th><th>Result/Error</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
            """
            return mark_safe(html)
        except Exception as e:
            return f"Error parsing failures: {str(e)}"

    failure_details.short_description = "Failure Report"

########----------------------------------------------------------


from django.contrib.auth.models import Group
admin_site.register(Group)

# bottom of admin.py
from django_celery_results.admin import TaskResultAdmin, GroupResultAdmin
from django_celery_results.models import TaskResult, GroupResult

# Re-register the 3rd party models into your custom admin_site
try:
    admin_site.register(TaskResult, TaskResultAdmin)
    admin_site.register(GroupResult, GroupResultAdmin)
except admin.sites.AlreadyRegistered:
    pass