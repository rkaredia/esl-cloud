from django.contrib import admin, messages
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from .base import admin_site, CompanySecurityMixin, UIHelperMixin
from .mixins import StoreFilteredAdmin
from ..models import Gateway, TagHardware, ESLTag
from ..views import download_tag_template, preview_tag_import, bulk_map_tags_view
from ..tasks import update_tag_image_task
import time
import logging

logger = logging.getLogger(__name__)

@admin.register(Gateway, site=admin_site)
class GatewayAdmin(CompanySecurityMixin, UIHelperMixin, StoreFilteredAdmin):
    """Admin for Managing ESL Gateways."""
    list_display = ('estation_id', 'gateway_mac', 'store', 'is_active', 'created_at', 'updated_at', 'updated_by')
    list_editable = ('store', 'is_active')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')

@admin.register(TagHardware, site=admin_site)
class TagHardwareAdmin(admin.ModelAdmin):
    """Admin for Hardware specifications (Screen size, color, etc)."""
    list_display = ('model_number', 'width_px', 'height_px', 'color_scheme', 'display_size_inch', 'created_at', 'updated_at', 'updated_by')
    readonly_fields = ('updated_at', 'updated_by')
    def has_change_permission(self, request, obj=None): return request.user.is_superuser

@admin.register(ESLTag, site=admin_site)
class ESLTagAdmin(CompanySecurityMixin, UIHelperMixin, StoreFilteredAdmin):
    """Admin for individual ESL Tags and their mapping to products."""
    change_list_template = "admin/core/esltag/change_list.html"
    list_display = ('image_status', 'tag_mac', 'get_paired_info', 'last_sync_status', 'battery_level_display', 'hardware_spec', 'template_id', 'gateway', 'sync_button', 'aisle', 'section', 'shelf_row', 'updated_at', 'created_at', 'updated_by')
    search_fields = ('tag_mac', 'paired_product__name', 'paired_product__sku')
    list_editable = ('template_id', 'aisle', 'section', 'shelf_row')
    list_filter = ('sync_state', 'gateway__store', 'hardware_spec')
    autocomplete_fields = ['paired_product']
    readonly_fields = ('get_paired_info', 'image_preview_large', 'sync_state', 'last_image_gen_success', 'last_image_task_id', 'audit_log_link', 'updated_at', 'updated_by', 'created_at')

    fieldsets = (
        ('Hardware', {'fields': ('tag_mac', 'gateway', 'hardware_spec', 'battery_level')}),
        ('Pairing', {'description': 'Search for a product by SKU or Name below.', 'fields': ('paired_product',)}),
        ('Visuals', {'fields': ('template_id', 'image_preview_large', 'last_image_gen_success', 'sync_state', 'last_image_task_id', 'audit_log_link')}),
        ('Location', {'fields': ('aisle', 'section', 'shelf_row')}),
        ('Audit', {'fields': ('updated_by', 'updated_at', 'created_at')}),
    )

    actions = ['safe_delete', 'safe_regenerate_images', 'refresh_all_store_tags', 'set_template_v1', 'set_template_v2']

    def image_status(self, obj):
        try:
            if not obj.paired_product: return mark_safe('<span style="color:#94a3b8;">○ No Product</span>')
            color = "#059669" if obj.tag_image else "#ea580c"
            return mark_safe(f'<span style="color:{color}; font-weight:bold;">● {"Generated" if obj.tag_image else "Pending"}</span>')
        except: return "Error"
    image_status.short_description = "Image"
    image_status.admin_order_field = 'tag_image'

    def get_paired_info(self, obj):
        try:
            if obj.paired_product: return f"{obj.paired_product.sku} - {obj.paired_product.name}"
            return mark_safe('<i style="color: #94a3b8;">Unpaired</i>')
        except: return "Error"
    get_paired_info.short_description = "Paired Product"
    get_paired_info.admin_order_field = 'paired_product__name'

    def last_sync_status(self, obj):
        try:
            color_map = {'SUCCESS': '#059669', 'PROCESSING': '#2563eb', 'PUSHED': '#7c3aed', 'IDLE': '#a2a2a3', 'GEN_FAILED': '#f50000', 'PUSH_FAILED': '#f50000', 'IMAGE_READY': '#f5ac00', 'FAILED': '#f50000'}
            color = color_map.get(obj.sync_state, '#ea580c')
            status_text = obj.get_sync_state_display()
            if obj.last_image_gen_success and obj.sync_state == 'SUCCESS':
                status_text = f"✔ {obj.last_image_gen_success.strftime('%H:%M')}"
            return format_html('<span style="color: {}; font-weight: bold;">{}</span>', color, status_text)
        except: return "Error"
    last_sync_status.short_description = "Sync Status"
    last_sync_status.admin_order_field = 'sync_state'

    def battery_level_display(self, obj):
        try:
            val = obj.battery_level or 0
            color = "#059669" if val > 20 else "#dc2626"
            return format_html('<div style="width: 80px; background: #eee; border-radius: 3px; height: 10px; display: inline-block; margin-right: 5px;"><div style="width: {}%; background: {}; height: 10px; border-radius: 3px;"></div></div><small>{}%</small>', val, color, val)
        except: return "Error"
    battery_level_display.short_description = "Battery"
    battery_level_display.admin_order_field = 'battery_level'

    def image_preview_large(self, obj):
        try:
            if not obj.paired_product: return mark_safe('<i style="color: #94a3b8;">No product paired.</i>')
            if obj.tag_image: return format_html('<img src="{}?v={}" style="max-width: 400px; border: 2px solid #eee; border-radius: 12px;"/>', obj.tag_image.url, int(time.time()))
            return "Waiting for background generation..."
        except: return "Error loading image"
    image_preview_large.short_description = "Current Tag Image"

    def audit_log_link(self, obj):
        try:
            if not obj.last_image_task_id: return "No task record"
            url = reverse('admin:django_celery_results_taskresult_changelist') + f"?task_id={obj.last_image_task_id}"
            return format_html('<a href="{}" target="_blank">View Task Results ↗</a>', url)
        except: return obj.last_image_task_id
    audit_log_link.short_description = "Audit Trail"

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

    def manual_sync_view(self, request, object_id):
        try:
            update_tag_image_task.delay(object_id)
            messages.success(request, "Sync task queued.")
        except Exception as e:
            logger.exception(f"Manual sync failed for {object_id}")
            messages.error(request, "Failed to queue sync.")
        return redirect(request.META.get('HTTP_REFERER', 'admin:index'))

    def get_urls(self):
        return [
            path('download-template/', self.admin_site.admin_view(download_tag_template), name='download_tag_template'),
            path('bulk-map/', self.admin_site.admin_view(bulk_map_tags_view), name='bulk-map-tags'),
            path('import-preview/', self.admin_site.admin_view(preview_tag_import), name='preview_tag_import'),
            path('<path:object_id>/sync/', self.admin_site.admin_view(self.manual_sync_view), name='sync-tag-manual'),
        ] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, extra_context=extra_context)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions: del actions['delete_selected']
        return actions
