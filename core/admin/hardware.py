from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
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

class BatteryLevelFilter(admin.SimpleListFilter):
    """Filter for ESL tags by battery health."""
    title = 'Battery Health'
    parameter_name = 'battery_health'

    def lookups(self, request, model_admin):
        return (
            ('critical', 'Critical (<5%)'),
            ('low', 'Low (<20%)'),
            ('good', 'Good (>20%)'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'critical':
            return queryset.filter(battery_level__lte=5)
        if self.value() == 'low':
            return queryset.filter(battery_level__lte=20)
        if self.value() == 'good':
            return queryset.filter(battery_level__gt=20)
        return queryset

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
        'is_online_status', 'gateway_mac', 'gateway_ip', 'last_heartbeat',
        'last_successful_heartbeat', 'last_seen', 'created_at',
        'updated_at', 'updated_by', 'ap_type', 'ap_version',
        'module_version', 'disk_size', 'free_space', 'heartbeat_interval',
        'tags_queued_count', 'tags_comm_count', 'last_error_message',
        'last_error_code', 'last_error_timestamp', 'status_indicator_large'
    )

    # Organized layout for the detailed edit page
    ordering = ('-last_heartbeat',)

    fieldsets = (
        ('General', {'fields': ('estation_id', 'name', 'alias', 'store')}),
        ('Technical', {'fields': (
            'gateway_mac', 'gateway_ip', 'app_server_ip', 'app_server_port',
            'ap_type', 'ap_version', 'module_version', 'disk_size', 'free_space', 'heartbeat_interval'
        )}),
        ('Monitoring', {'fields': ('tags_queued_count', 'tags_comm_count', 'last_error_message', 'last_error_code', 'last_error_timestamp')}),
        ('Credentials', {'fields': ('username', 'password')}),
        ('Network Settings', {
            'classes': ('collapse',), # Collapsed by default to hide complexity
            'fields': ('is_auto_ip', 'local_ip', 'netmask', 'network_gateway', 'is_encrypt_enabled')
        }),
        ('Status', {'fields': ('status_indicator_large', 'is_online_status', 'last_heartbeat', 'last_successful_heartbeat', 'last_seen')}),
        ('Audit', {'fields': ('created_at', 'updated_at', 'updated_by')}),
    )

    def status_indicator(self, obj):
        """Visual Dot showing online status with descriptive labels."""
        status_code, text, color = obj.get_real_time_status()
        return format_html('<span style="color: {}; font-weight: bold;"><span aria-hidden="true">●</span> {}</span>', color, text)
    status_indicator.short_description = "Status"

    def status_indicator_large(self, obj):
        """Visual Dot for detail view."""
        return self.status_indicator(obj)
    status_indicator_large.short_description = "Real-time Status"

    def is_online_status(self, obj):
        """Standard display for the is_online status field to avoid boolean icon confusion."""
        return obj.get_is_online_display()
    is_online_status.short_description = "Last Reported Status"

    def configure_link(self, obj):
        """Link to the 'Remote Config' page for this gateway."""
        if not obj.estation_id: return "-"
        url = reverse('admin:gateway-configure', args=[obj.pk])
        return format_html('<a class="button" href="{}">⚙ Config</a>', url)
    configure_link.short_description = "Configuration"

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """
        Security: Use a PasswordInput widget for the password field.
        We set render_value=False to ensure the plain-text password is never
        sent to the browser in the HTML source.
        """
        from django import forms
        if db_field.name == 'password':
            kwargs['widget'] = forms.PasswordInput(render_value=False)
            kwargs['help_text'] = "Leave blank to keep the current password."
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        """
        Security: Ensure we don't overwrite the password with an empty string
        if the user leaves the password field blank (since we don't render it).
        """
        if change and not form.cleaned_data.get('password'):
            # Re-fetch the existing password from the database
            obj.password = Gateway.objects.get(pk=obj.pk).password
        super().save_model(request, obj, form, change)

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
    list_filter = ('sync_state', 'gateway__store', 'hardware_spec', BatteryLevelFilter)

    # UI Enhancement: Search-as-you-type for product pairing
    autocomplete_fields = ['paired_product']

    ordering = ('-updated_at',)

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

    actions = [
        'safe_regenerate_images', 'refresh_all_store_tags',
        'set_all_template_v1', 'set_all_template_v2', 'set_all_template_v3',
        'safe_delete'
    ]

    def get_queryset(self, request):
        """Optimize performance by prefetching related objects and adding custom sorting."""
        from django.db.models import Case, When, Value, IntegerField
        qs = super().get_queryset(request).select_related(
            'paired_product', 'gateway', 'hardware_spec', 'store'
        )
        # Custom sorting: 0: Generated (Green), 1: Pending (Orange), 2: No Product (Gray)
        qs = qs.annotate(
            image_sort_val=Case(
                When(paired_product__isnull=True, then=Value(2)),
                When(tag_image__isnull=False, tag_image__gt='', then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        return qs

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
    image_status.admin_order_field = 'image_sort_val'

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
                'IMAGE_READY': '#f5ac00', 'FAILED': '#f50000', 'RETRY_WAITING': '#f5ac00'
            }
            color = color_map.get(obj.sync_state, '#ea580c')
            status_text = obj.get_sync_state_display()
            if obj.sync_state == 'RETRY_WAITING':
                status_text = f"Retrying ({obj.retry_count}/3)"
            elif obj.last_image_gen_success and obj.sync_state == 'SUCCESS':
                status_text = "Success - Tag Updated"
            return format_html('<span style="color: {}; font-weight: bold;">{}</span>', color, status_text)
        except: return "Error"
    last_sync_status.short_description = "Sync Status"
    last_sync_status.admin_order_field = 'sync_state'

    def battery_level_display(self, obj):
        """Renders a visual progress bar for battery life."""
        try:
            val = obj.battery_level or 0
            if val > 20:
                color = "#059669" # Good (Green)
            elif val > 5:
                color = "#d97706" # Low (Orange)
            else:
                color = "#dc2626" # Critical (Red)

            return format_html(
                '<div role="progressbar" aria-valuenow="{}" aria-valuemin="0" aria-valuemax="100" title="Battery Level: {}%" '
                'style="width: 80px; background: #eee; border-radius: 3px; height: 10px; display: inline-block; margin-right: 5px;">'
                '<div style="width: {}%; background: {}; height: 10px; border-radius: 3px; transition: width 0.5s ease, background-color 0.5s ease;"></div>'
                '</div><small aria-hidden="true">{}%</small>',
                val, val, val, color, val
            )
        except: return "Error"
    battery_level_display.short_description = "Battery"
    battery_level_display.admin_order_field = 'battery_level'

    def image_preview_large(self, obj):
        """Shows the actual BMP image that is currently on the tag."""
        try:
            if not obj.paired_product: return format_html('<i style="color: #94a3b8;">No product paired.</i>')
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


    # CUSTOM VIEW METHODS
    def manual_sync_view(self, request, object_id):
        """Logic for the 'Sync' button in the list view."""
        if not request.user.has_perm('core.change_esltag'):
            raise PermissionDenied

        tag = self.get_queryset(request).filter(pk=object_id).first()
        if not tag:
            messages.error(request, "Permission denied.")
            return redirect('admin:index')

        # GATEWAY STATUS VALIDATION
        if tag.gateway:
            status, label, _ = tag.gateway.get_real_time_status()
            if status == 'OFFLINE':
                messages.warning(request, f"Warning: Gateway {tag.gateway.estation_id} is OFFLINE. Update may be delayed.")
            elif status == 'ERROR':
                messages.warning(request, f"Warning: Gateway {tag.gateway.estation_id} is reporting an ERROR ({label}).")
            else:
                messages.success(request, f"Sync task queued for {tag.tag_mac} via Gateway {tag.gateway.estation_id}.")
        else:
            messages.warning(request, "Warning: This tag has no assigned gateway. It will try to find one automatically.")

        update_tag_image_task.delay(object_id) # Queue the task
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

    @admin.action(description="Refresh selected (Max 100)")
    def safe_regenerate_images(self, request, queryset):
        if not request.user.has_perm('core.change_esltag'):
            raise PermissionDenied
        try:
            count = queryset.count()
            if count > 100:
                self.message_user(request, "Error: Max 100 tags allowed.", messages.ERROR)
                return

            offline_gateways = set()
            for tag in queryset:
                if tag.gateway:
                    status, _, _ = tag.gateway.get_real_time_status()
                    if status == 'OFFLINE':
                        offline_gateways.add(tag.gateway.estation_id)
                update_tag_image_task.delay(tag.id)

            if offline_gateways:
                self.message_user(
                    request,
                    f"Queued {count} tags for refresh. Note: {len(offline_gateways)} gateways are currently OFFLINE ({', '.join(offline_gateways)}).",
                    messages.WARNING
                )
            else:
                self.message_user(request, f"Queued {count} tags for refresh.")
        except Exception as e:
            logger.exception("Error in safe_regenerate_images")
            self.message_user(request, "Failed to queue refresh.", messages.ERROR)

    @admin.action(description="Delete selected tags (Max 100)")
    def safe_delete(self, request, queryset):
        try:
            count = queryset.count()
            if count > 100:
                self.message_user(request, "Error: Max 100 tags allowed.", messages.ERROR)
                return
            queryset.delete()
            self.message_user(request, f"Deleted {count} tags.")
        except Exception as e:
            logger.exception("Error in safe_delete action")
            self.message_user(request, "Technical error during deletion.", messages.ERROR)

    @admin.action(description="Refresh ALL Images")
    def refresh_all_store_tags(self, request, queryset):
        if not request.user.has_perm('core.change_esltag'):
            raise PermissionDenied
        try:
            if not request.active_store:
                self.message_user(request, "Please select a store first.", messages.WARNING)
                return
            tags = ESLTag.objects.for_store(request.active_store).filter(paired_product__isnull=False)
            count = tags.count()
            # Safety limit: max 500 for ALL refresh to prevent queue flooding
            for tag in tags[:500]: update_tag_image_task.delay(tag.id)
            self.message_user(request, f"Queued refresh for {min(count, 500)} tags in {request.active_store.name}.")
        except Exception as e:
            logger.exception("Error in refresh_all_store_tags")
            self.message_user(request, "Failed to trigger image refresh.", messages.ERROR)

    @admin.action(description="Set ALL Image Template - V1")
    def set_all_template_v1(self, request, queryset):
        if not request.user.has_perm('core.change_esltag'):
            raise PermissionDenied
        try:
            if not request.active_store:
                self.message_user(request, "Please select a store first.", messages.WARNING)
                return
            tags = ESLTag.objects.for_store(request.active_store)
            count = tags.count()
            tags.update(template_id=1)
            # Sync only tags with products
            sync_tags = tags.filter(paired_product__isnull=False)
            for tag in sync_tags[:500]: update_tag_image_task.delay(tag.id)
            self.message_user(request, f"Updated {count} tags to V1 and queued sync for paired tags.")
        except Exception as e:
            logger.exception("Error in set_all_template_v1")
            self.message_user(request, "Error updating templates.", messages.ERROR)

    @admin.action(description="Set ALL Image Template - V2")
    def set_all_template_v2(self, request, queryset):
        if not request.user.has_perm('core.change_esltag'):
            raise PermissionDenied
        try:
            if not request.active_store:
                self.message_user(request, "Please select a store first.", messages.WARNING)
                return
            tags = ESLTag.objects.for_store(request.active_store)
            count = tags.count()
            tags.update(template_id=2)
            sync_tags = tags.filter(paired_product__isnull=False)
            for tag in sync_tags[:500]: update_tag_image_task.delay(tag.id)
            self.message_user(request, f"Updated {count} tags to V2 and queued sync for paired tags.")
        except Exception as e:
            logger.exception("Error in set_all_template_v2")
            self.message_user(request, "Error updating templates.", messages.ERROR)

    @admin.action(description="Set ALL Image Template - V3")
    def set_all_template_v3(self, request, queryset):
        if not request.user.has_perm('core.change_esltag'):
            raise PermissionDenied
        try:
            if not request.active_store:
                self.message_user(request, "Please select a store first.", messages.WARNING)
                return
            tags = ESLTag.objects.for_store(request.active_store)
            count = tags.count()
            tags.update(template_id=3)
            sync_tags = tags.filter(paired_product__isnull=False)
            for tag in sync_tags[:500]: update_tag_image_task.delay(tag.id)
            self.message_user(request, f"Updated {count} tags to V3 and queued sync for paired tags.")
        except Exception as e:
            logger.exception("Error in set_all_template_v3")
            self.message_user(request, "Error updating templates.", messages.ERROR)
