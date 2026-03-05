from django.contrib import admin
from django.utils.html import format_html
from django_celery_results.models import TaskResult, GroupResult
from django_celery_results.admin import GroupResultAdmin, TaskResultAdmin
from .base import admin_site
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
