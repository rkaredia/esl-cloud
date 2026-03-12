from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from .base import admin_site, CompanySecurityMixin, UIHelperMixin
from .mixins import StoreFilteredAdmin
from ..models import Gateway, TagHardware, ESLTag
from ..views import download_tag_template, preview_tag_import, bulk_map_tags_view, configure_gateway_view
from ..tasks import update_tag_image_task
import time
import logging

"""
HARDWARE & CONNECTIVITY ADMIN
-----------------------------
This module provides the management interface for the physical IOT devices:
Gateways (eStations), Tag Hardware specs, and the ESL Tags themselves.

Features:
- REAL-TIME STATUS: Visual indicators showing if a gateway is online.
- REMOTE CONFIG: Buttons to push network settings to gateways via MQTT.
- TAG MONITORING: Tracking battery levels and sync states for every label.
- BULK PAIRING: Custom view for mapping products to tags using a barcode scanner.
"""

logger = logging.getLogger(__name__)

@admin.register(Gateway, site=admin_site)
class GatewayAdmin(CompanySecurityMixin, UIHelperMixin, StoreFilteredAdmin):
    """
    GATEWAY (eSTATION) MANAGEMENT
    -----------------------------
    Used to track the physical base stations.
    Note: Many fields are 'Read-Only' because they are updated automatically
    via MQTT heartbeats from the hardware.
    """
    list_display = (
        'status_indicator', 'estation_id', 'name', 'alias',
        'gateway_mac', 'gateway_ip', 'store', 'last_heartbeat',
        'configure_link'
    )
    list_editable = ('name',)

    # These fields are technical telemetry and shouldn't be edited by hand.
    readonly_fields = (
        'is_online', 'gateway_mac', 'gateway_ip', 'last_heartbeat',
        'last_successful_heartbeat', 'last_seen', 'created_at',
        'updated_at', 'updated_by', 'ap_type', 'ap_version',
        'module_version', 'disk_size', 'free_space', 'heartbeat_interval'
    )

    # Organized layout for the detailed edit page
    fieldsets = (
        ('General', {'fields': ('estation_id', 'name', 'alias', 'store')}),
        ('Technical', {'fields': (
            'gateway_mac', 'gateway_ip', 'app_server_ip', 'app_server_port',
            'ap_type', 'ap_version', 'module_version', 'disk_size', 'free_space', 'heartbeat_interval'
        )}),
        ('Credentials', {'fields': ('username', 'password')}),
        ('Network Settings', {
            'classes': ('collapse',), # Collapsed by default to hide complexity
            'fields': ('is_auto_ip', 'local_ip', 'netmask', 'network_gateway', 'is_encrypt_enabled')
        }),
        ('Status', {'fields': ('is_online', 'last_heartbeat', 'last_successful_heartbeat', 'last_seen')}),
        ('Audit', {'fields': ('created_at', 'updated_at', 'updated_by')}),
    )

    def status_indicator(self, obj):
        """Visual Dot showing online status."""
        color = "#059669" if obj.is_online else "#dc2626"
        text = "Online" if obj.is_online else "Offline"
        return format_html('<span style="color: {}; font-weight: bold;"><span aria-hidden="true">●</span> {}</span>', color, text)
    status_indicator.short_description = "Status"

    def configure_link(self, obj):
        """Link to the 'Remote Config' page for this gateway."""
        if not obj.estation_id: return "-"
        url = reverse('admin:gateway-configure', args=[obj.pk])
        return format_html('<a class="button" href="{}">⚙ Config</a>', url)
    configure_link.short_description = "Configuration"

    # PERMISSION OVERRIDES:
    # Only superusers can see credentials or perform destructive actions.
    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if not request.user.is_superuser:
            fields = [f for f in fields if f not in ['password', 'username']]
        return fields

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

@admin.register(TagHardware, site=admin_site)
class TagHardwareAdmin(admin.ModelAdmin):
    """
    HARDWARE DICTIONARY
    -------------------
    Defines physical specs like '2.13 inch' or 'BWR color'.
    """
    list_display = ('model_number', 'width_px', 'height_px', 'color_scheme', 'display_size_inch')
    readonly_fields = ('updated_at', 'updated_by')

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

@admin.register(ESLTag, site=admin_site)
class ESLTagAdmin(CompanySecurityMixin, UIHelperMixin, StoreFilteredAdmin):
    """
    ESL TAG REGISTRY (THE LABELS)
    -----------------------------
    The most used page in the system. It links Products to MAC addresses.
    """
    change_list_template = "admin/core/esltag/change_list.html"

    list_display = (
        'image_status', 'tag_mac', 'paired_product', 'last_sync_status',
        'battery_level_display', 'hardware_spec', 'template_id', 'gateway',
        'last_successful_gateway_id', 'sync_button', 'aisle', 'section',
        'shelf_row', 'updated_at'
    )

    # SKU/Name search for quick finding
    search_fields = ('tag_mac', 'paired_product__name', 'paired_product__sku')

    # Rapid data entry: Edit location and pairing directly in the list
    list_editable = ('paired_product', 'template_id', 'aisle', 'section', 'shelf_row')

    # Filters on the right sidebar
    list_filter = ('sync_state', 'gateway__store', 'hardware_spec')

    # UI Enhancement: Search-as-you-type for product pairing
    autocomplete_fields = ['paired_product']

    readonly_fields = (
        'get_paired_info', 'image_preview_large', 'sync_state',
        'last_image_gen_success', 'last_image_task_id', 'audit_log_link',
        'updated_at', 'updated_by', 'created_at', 'last_successful_gateway_id', 'gateway'
    )

    fieldsets = (
        ('Hardware', {'fields': ('tag_mac', 'gateway', 'last_successful_gateway_id', 'hardware_spec', 'battery_level')}),
        ('Pairing', {'description': 'Search for a product by SKU or Name below.', 'fields': ('paired_product',)}),
        ('Visuals', {'fields': (
            'template_id', 'image_preview_large', 'last_image_gen_success',
            'sync_state', 'last_image_task_id', 'audit_log_link'
        )}),
        ('Location', {'fields': ('aisle', 'section', 'shelf_row')}),
        ('Audit', {'fields': ('updated_by', 'updated_at', 'created_at')}),
    )

    actions = ['safe_delete', 'safe_regenerate_images', 'refresh_all_store_tags', 'set_template_v1', 'set_template_v2']

    def image_status(self, obj):
        """Visual status indicator for image generation."""
        try:
            if not obj.paired_product:
                return format_html('<span style="color:#94a3b8;"><span aria-hidden="true">○</span> No Product</span>')
            color = "#059669" if obj.tag_image else "#ea580c"
            status_text = "Generated" if obj.tag_image else "Pending"
            return format_html('<span style="color:{}; font-weight:bold;"><span aria-hidden="true">●</span> {}</span>', color, status_text)
        except: return "Error"
    image_status.short_description = "Image"
    image_status.admin_order_field = 'tag_image'

    def get_paired_info(self, obj):
        """Formatted product info for read-only displays."""
        try:
            if obj.paired_product: return f"{obj.paired_product.sku} - {obj.paired_product.name}"
            return format_html('<i style="color: #94a3b8;">Unpaired</i>')
        except: return "Error"
    get_paired_info.short_description = "Paired Product"
    get_paired_info.admin_order_field = 'paired_product__name'

    def last_sync_status(self, obj):
        """Visual status of the last hardware update."""
        try:
            color_map = {
                'SUCCESS': '#059669', 'PROCESSING': '#2563eb', 'PUSHED': '#7c3aed',
                'IDLE': '#a2a2a3', 'GEN_FAILED': '#f50000', 'PUSH_FAILED': '#f50000',
                'IMAGE_READY': '#f5ac00', 'FAILED': '#f50000'
            }
            color = color_map.get(obj.sync_state, '#ea580c')
            status_text = obj.get_sync_state_display()
            if obj.last_image_gen_success and obj.sync_state == 'SUCCESS':
                status_text = f"✔ {obj.last_image_gen_success.strftime('%H:%M')}"
            return format_html('<span style="color: {}; font-weight: bold;">{}</span>', color, status_text)
        except: return "Error"
    last_sync_status.short_description = "Sync Status"
    last_sync_status.admin_order_field = 'sync_state'

    def battery_level_display(self, obj):
        """Renders a visual progress bar for battery life."""
        try:
            val = obj.battery_level or 0
            color = "#059669" if val > 20 else "#dc2626"
            return format_html(
                '<div role="progressbar" aria-valuenow="{}" aria-valuemin="0" aria-valuemax="100" title="Battery Level: {}%" '
                'style="width: 80px; background: #eee; border-radius: 3px; height: 10px; display: inline-block; margin-right: 5px;">'
                '<div style="width: {}%; background: {}; height: 10px; border-radius: 3px;"></div>'
                '</div><small aria-hidden="true">{}%</small>',
                val, val, val, color, val
            )
        except: return "Error"
    battery_level_display.short_description = "Battery"
    battery_level_display.admin_order_field = 'battery_level'

    def image_preview_large(self, obj):
        """Shows the actual BMP image that is currently on the tag."""
        try:
            if not obj.paired_product: return mark_safe('<i style="color: #94a3b8;">No product paired.</i>')
            if obj.tag_image:
                return format_html('<img src="{}?v={}" style="max-width: 400px; border: 2px solid #eee; border-radius: 12px;"/>', obj.tag_image.url, int(time.time()))
            return "Waiting for background generation..."
        except: return "Error loading image"
    image_preview_large.short_description = "Current Tag Image"

    def audit_log_link(self, obj):
        """Link to the Celery Task record for debugging generation failures."""
        try:
            if not obj.last_image_task_id: return "No task record"
            url = reverse('admin:django_celery_results_taskresult_changelist') + f"?task_id={obj.last_image_task_id}"
            return format_html('<a href="{}" target="_blank">View Task Results ↗</a>', url)
        except: return obj.last_image_task_id
    audit_log_link.short_description = "Audit Trail"

    def get_queryset(self, request):
        """
        Optimize the list view by pre-fetching related data.
        Reduces query count from N+1 to 1 for the main list results.
        """
        qs = super().get_queryset(request)
        return qs.select_related('paired_product', 'hardware_spec', 'gateway')

    # CUSTOM VIEW METHODS
    def manual_sync_view(self, request, object_id):
        """Logic for the 'Sync' button in the list view."""
        if not self.get_queryset(request).filter(pk=object_id).exists():
            messages.error(request, "Permission denied.")
            return redirect('admin:index')

        update_tag_image_task.delay(object_id) # Queue the task
        messages.success(request, "Sync task queued.")
        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def get_urls(self):
        """Register auxiliary URLs for templates, imports, and manual sync."""
        return [
            path('download-template/', self.admin_site.admin_view(download_tag_template), name='download_tag_template'),
            path('bulk-map/', self.admin_site.admin_view(bulk_map_tags_view), name='bulk-map-tags'),
            path('import-preview/', self.admin_site.admin_view(preview_tag_import), name='preview_tag_import'),
            path('<path:object_id>/sync/', self.admin_site.admin_view(self.manual_sync_view), name='sync-tag-manual'),
            path('<path:gateway_id>/configure/', self.admin_site.admin_view(configure_gateway_view), name='gateway-configure'),
        ] + super().get_urls()

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions: del actions['delete_selected']
        return actions

    @admin.action(description="Regenerate selected (Max 100)")
    def safe_regenerate_images(self, request, queryset):
        try:
            count = queryset.count()
            if count > 100:
                self.message_user(request, "Error: Max 100 tags allowed.", messages.ERROR)
                return
            for tag in queryset: update_tag_image_task.delay(tag.id)
            self.message_user(request, f"Queued {count} tags for regeneration.")
        except Exception as e:
            logger.exception("Error in safe_regenerate_images")
            self.message_user(request, "Failed to queue regeneration.", messages.ERROR)

    @admin.action(description="Delete selected (Max 100)")
    def safe_delete(self, request, queryset):
        try:
            count = queryset.count()
            if count > 100:
                self.message_user(request, "Error: Max 100 items allowed.", messages.ERROR)
                return
            queryset.delete()
            self.message_user(request, f"Deleted {count} tags.")
        except Exception as e:
            logger.exception("Error in safe_delete action")
            self.message_user(request, "Technical error during deletion.", messages.ERROR)

    @admin.action(description="Refresh ALL tags in Store")
    def refresh_all_store_tags(self, request, queryset):
        try:
            if not request.active_store:
                self.message_user(request, "Please select a store first.", messages.WARNING)
                return
            tags = ESLTag.objects.for_store(request.active_store).filter(paired_product__isnull=False)
            count = tags.count()
            for tag in tags[:200]: update_tag_image_task.delay(tag.id)
            self.message_user(request, f"Queued refresh for {min(count, 200)} tags.")
        except Exception as e:
            logger.exception("Error in refresh_all_store_tags")
            self.message_user(request, "Failed to trigger store refresh.", messages.ERROR)

    @admin.action(description="Set template to Standard (V1)")
    def set_template_v1(self, request, queryset):
        try:
            count = queryset.count()
            queryset.update(template_id=1)
            for tag in queryset: update_tag_image_task.delay(tag.id)
            self.message_user(request, f"Updated {count} tags to V1 and queued sync.")
        except Exception as e:
            logger.exception("Error in set_template_v1")
            self.message_user(request, "Error updating templates.", messages.ERROR)

    @admin.action(description="Set template to High-Visibility (V2)")
    def set_template_v2(self, request, queryset):
        try:
            count = queryset.count()
            queryset.update(template_id=2)
            for tag in queryset: update_tag_image_task.delay(tag.id)
            self.message_user(request, f"Updated {count} tags to V2 and queued sync.")
        except Exception as e:
            logger.exception("Error in set_template_v2")
            self.message_user(request, "Error updating templates.", messages.ERROR)
