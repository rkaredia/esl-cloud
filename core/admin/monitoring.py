from django.contrib import admin, messages
from django.utils.html import format_html
from django_celery_results.models import TaskResult, GroupResult
from django_celery_results.admin import GroupResultAdmin, TaskResultAdmin
from .base import admin_site, CompanySecurityMixin
from ..models import MQTTMessage
import json

"""
SYSTEM MONITORING & LOGGING ADMIN
---------------------------------
Provides visibility into the background processes and physical
hardware communication.

Key Areas:
1. CELERY RESULTS: Tracks the progress of background image generation.
2. MQTT LOGS: Shows every packet sent or received from the eStations.
3. DATA FORMATTING: Uses 'format_html' and '<pre>' tags to make raw
   JSON logs readable for humans.
"""

# Re-register standard Celery Result models into our custom SAIS Admin Site
try:
    admin.site.unregister(GroupResult)
    admin.site.unregister(TaskResult)
except admin.sites.NotRegistered:
    pass

@admin.register(GroupResult, site=admin_site)
class CustomGroupResultAdmin(GroupResultAdmin):
    """
    BULK OPERATION MONITORING
    -------------------------
    When you refresh 500 tags, Celery creates a 'Group'. This view
    shows the aggregate progress (e.g., "80% Complete").
    """
    list_display = ('group_id', 'batch_progress', 'date_done')

    def batch_progress(self, obj):
        """Calculates percentage of successful vs failed tasks in a group."""
        try:
            task_ids = json.loads(obj.result) if isinstance(obj.result, str) else obj.result
            total = len(task_ids)
            queryset = TaskResult.objects.filter(task_id__in=task_ids)
            completed = queryset.filter(status='SUCCESS').count()
            failed = queryset.filter(status='FAILURE').count()
            percent = int(((completed + failed) / total) * 100)
            return format_html("<b>{}%</b> (✅ {} | ❌ {} | Total {})", percent, completed, failed, total)
        except: return "Pending"

class CustomTaskResultAdmin(TaskResultAdmin):
    """
    CELERY TASK MONITORING
    ----------------------
    Restricted to read-only access to prevent manual task creation.
    """
    def has_add_permission(self, request): return False

# Register custom task monitoring
admin_site.register(TaskResult, CustomTaskResultAdmin)

@admin.register(MQTTMessage, site=admin_site)
class MQTTMessageAdmin(CompanySecurityMixin, admin.ModelAdmin):
    """
    MQTT PACKET INSPECTOR
    ---------------------
    The most technical view in the platform. Shows raw binary/JSON
    exchange with the physical hardware.
    """
    list_display = (
        'timestamp', 'direction_indicator', 'estation_id', 'tag_id_column',
        'topic', 'data_preview', 'status_indicator'
    )
    list_filter = ('direction', 'is_success', 'estation_id', 'topic')
    search_fields = ('estation_id', 'topic', 'data')

    # Read-only because logs are immutable history records.
    readonly_fields = ('timestamp', 'direction', 'estation_id', 'topic', 'data_json', 'is_success')
    ordering = ('-timestamp',)

    def data_json(self, obj):
        """Renders the raw JSON string as a pretty-printed, scrollable code block."""
        try:
            parsed = json.loads(obj.data)
            formatted = json.dumps(parsed, indent=2)
            # Increased max-height and improved readability for large payloads (like Base64 images)
            return format_html('<pre style="background: #f8fafc; padding: 10px; border-radius: 6px; border: 1px solid #e2e8f0; font-family: monospace; font-size: 0.9em; max-height: 600px; overflow: auto; white-space: pre-wrap; word-break: break-all;">{}</pre>', formatted)
        except:
            return obj.data
    data_json.short_description = "Formatted Payload"

    def direction_indicator(self, obj):
        """Blue for Sent, Purple for Received."""
        color = "#2563eb" if obj.direction == "sent" else "#7c3aed"
        return format_html('<span style="background: {}; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em;">{}</span>', color, obj.direction.upper())
    direction_indicator.short_description = "Dir"

    def status_indicator(self, obj):
        if obj.is_success:
            color = "#059669" # Green
            text = "SUCCESS"
        else:
            # Check if it's a partial failure (some tags succeeded, some failed)
            is_partial = False
            if obj.topic.endswith("/result"):
                try:
                    data = json.loads(obj.data)
                    if isinstance(data, list) and len(data) >= 5 and isinstance(data[4], list):
                        status_codes = [tr[4] for tr in data[4] if isinstance(tr, list) and len(tr) >= 5]
                        success_count = sum(1 for s in status_codes if s == 0 or s == 128)
                        if success_count > 0 and success_count < len(status_codes):
                            is_partial = True
                except: pass

            if is_partial:
                color = "#f59e0b" # Amber/Orange
                text = "PARTIAL FAILURE"
            else:
                color = "#dc2626" # Red
                text = "FAILURE"

        return format_html('<span style="color: {}; font-weight: bold;"><span aria-hidden="true">●</span> {}</span>', color, text)
    status_indicator.short_description = "Status"

    def tag_id_column(self, obj):
        """
        Extracts and displays the ESL Tag ID from the payload.
        Handles complex nested hardware lists and dictionary formats.
        For result messages, it appends the status to each tag ID.
        """
        def clean_mac(val):
            if not isinstance(val, (str, bytes)): return None
            s = val.decode('utf-8', errors='ignore') if isinstance(val, bytes) else val
            c = s.replace(':', '').upper()
            if len(c) == 12 and all(char in '0123456789ABCDEF' for char in c):
                return c
            return None

        try:
            data = json.loads(obj.data)
            tags_with_status = []

            # SPECIAL CASE: Result topic with potentially multiple tags
            if obj.topic.endswith("/result"):
                if isinstance(data, list):
                    # Multi-tag format: [Port, Wait, Send, Msg, [Tags]]
                    if len(data) >= 5 and isinstance(data[4], list):
                        for tr in data[4]:
                            if isinstance(tr, list) and len(tr) >= 5:
                                mac = clean_mac(tr[0])
                                if mac:
                                    status = "Success" if (tr[4] == 0 or tr[4] == 128) else "Failure"
                                    tags_with_status.append(f"{mac}-{status}")
                    # Single-tag format: [TagID, Rf, Batt, Ver, Status, ...]
                    else:
                        d = data[0] if len(data) == 1 and isinstance(data[0], list) else data
                        if isinstance(d, list) and len(d) >= 5:
                            mac = clean_mac(d[0])
                            if mac:
                                status = "Success" if (d[4] == 0 or d[4] == 128) else "Failure"
                                tags_with_status.append(f"{mac}-{status}")

            if tags_with_status:
                html_items = [format_html('<code style="font-weight: bold; color: {};">{}</code>',
                                         "#059669" if "Success" in ts else "#dc2626", ts)
                              for ts in tags_with_status]
                return format_html(", ".join(["{}"] * len(html_items)), *html_items)

            # FALLBACK: Recursive search for any tag ID (for other topics)
            def find_tag_id(item):
                mac = clean_mac(item)
                if mac: return mac
                if isinstance(item, list):
                    for sub in item:
                        found = find_tag_id(sub)
                        if found: return found
                elif isinstance(item, dict):
                    for key in ['TagId', 'tag_id', 'Tags']:
                        found = find_tag_id(item.get(key))
                        if found: return found
                    for val in item.values():
                        found = find_tag_id(val)
                        if found: return found
                return None

            tag_id = find_tag_id(data)
            if tag_id:
                return format_html('<code style="font-weight: bold; color: #0f172a;">{}</code>', tag_id)
            return "-"
        except:
            return "-"
    tag_id_column.short_description = "ESL Tag ID"

    def data_preview(self, obj):
        """Short snippet of the payload for the main table."""
        try:
            val = obj.data
            if len(val) > 80: val = val[:77] + "..."
            return format_html('<code style="font-family: monospace; font-size: 0.9em; background: #f1f5f9; padding: 2px 4px; border-radius: 3px;">{}</code>', val)
        except: return "-"
    data_preview.short_description = "Payload Preview"

    # ACTIONS
    actions = ['clear_all_messages']

    @admin.action(description="Clear all communication logs")
    def clear_all_messages(self, request, queryset):
        """Bulk deletion of logs - restricted to Superusers."""
        if not request.user.is_superuser:
            self.message_user(request, "Only superusers can clear communication logs.", messages.ERROR)
            return

        # Use get_queryset to ensure only authorized messages are cleared (defense-in-depth)
        qs = self.get_queryset(request)
        count = qs.count()
        qs.delete()
        self.message_user(request, f"Cleared {count} messages.", messages.SUCCESS)

    # Prevent manual creation or editing of log entries
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
