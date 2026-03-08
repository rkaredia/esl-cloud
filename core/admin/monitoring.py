from django.contrib import admin, messages
from django.utils.html import format_html
from django_celery_results.models import TaskResult, GroupResult
from django_celery_results.admin import GroupResultAdmin, TaskResultAdmin
from .base import admin_site
from ..models import MQTTMessage
import json

try:
    admin.site.unregister(GroupResult)
    admin.site.unregister(TaskResult)
except admin.sites.NotRegistered:
    pass

@admin.register(GroupResult, site=admin_site)
class CustomGroupResultAdmin(GroupResultAdmin):
    """Admin for viewing Celery Group results (Bulk operations)."""
    list_display = ('group_id', 'batch_progress', 'date_done')

    def batch_progress(self, obj):
        try:
            task_ids = json.loads(obj.result) if isinstance(obj.result, str) else obj.result
            total = len(task_ids)
            queryset = TaskResult.objects.filter(task_id__in=task_ids)
            completed = queryset.filter(status='SUCCESS').count()
            failed = queryset.filter(status='FAILURE').count()
            percent = int(((completed + failed) / total) * 100)
            return format_html("<b>{}%</b> (✅ {} | ❌ {} | Total {})", percent, completed, failed, total)
        except: return "Pending"

admin_site.register(TaskResult, TaskResultAdmin)

@admin.register(MQTTMessage, site=admin_site)
class MQTTMessageAdmin(admin.ModelAdmin):
    """Admin for viewing MQTT communication logs."""
    list_display = ('timestamp', 'direction_indicator', 'estation_id', 'topic', 'data_preview', 'status_indicator')
    list_filter = ('direction', 'is_success', 'estation_id', 'topic')
    search_fields = ('estation_id', 'topic', 'data')
    readonly_fields = ('timestamp', 'direction', 'estation_id', 'topic', 'data_json', 'is_success')
    ordering = ('-timestamp',)

    def data_json(self, obj):
        try:
            parsed = json.loads(obj.data)
            formatted = json.dumps(parsed, indent=2)
            return format_html('<pre style="background: #f8fafc; padding: 10px; border-radius: 6px; border: 1px solid #e2e8f0; font-family: monospace; font-size: 0.9em; max-height: 400px; overflow: auto;">{}</pre>', formatted)
        except:
            return obj.data
    data_json.short_description = "Formatted Payload"

    def direction_indicator(self, obj):
        color = "#2563eb" if obj.direction == "sent" else "#7c3aed"
        return format_html('<span style="background: {}; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em;">{}</span>', color, obj.direction.upper())
    direction_indicator.short_description = "Dir"

    def status_indicator(self, obj):
        color = "#059669" if obj.is_success else "#dc2626"
        text = "SUCCESS" if obj.is_success else "FAILURE"
        return format_html('<span style="color: {}; font-weight: bold;">● {}</span>', color, text)
    status_indicator.short_description = "Status"

    def data_preview(self, obj):
        try:
            val = obj.data
            if len(val) > 80: val = val[:77] + "..."
            return format_html('<code style="font-family: monospace; font-size: 0.9em; background: #f1f5f9; padding: 2px 4px; border-radius: 3px;">{}</code>', val)
        except: return "-"
    data_preview.short_description = "Payload Preview"

    actions = ['clear_all_messages']

    @admin.action(description="Clear all communication logs")
    def clear_all_messages(self, request, queryset):
        count = MQTTMessage.objects.all().count()
        MQTTMessage.objects.all().delete()
        self.message_user(request, f"Cleared {count} messages.", messages.SUCCESS)

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
