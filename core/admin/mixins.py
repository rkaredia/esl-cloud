from django.contrib import admin
from django.db import models
from ..managers import StoreManager

class StoreFilteredAdmin(admin.ModelAdmin):
    """Filters list views and foreign key dropdowns to only show data for the active store."""
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        active_store = getattr(request, 'active_store', None)
        if active_store:
            if hasattr(self.model.objects, 'for_store'):
                return self.model.objects.for_store(active_store)
        return qs if request.user.is_superuser else qs.none()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and hasattr(request, 'active_store'):
            if db_field.name == "gateway":
                kwargs["queryset"] = db_field.related_model.objects.for_store(request.active_store)
            if db_field.name == "paired_product":
                kwargs["queryset"] = db_field.related_model.objects.for_store(request.active_store)
            if db_field.name == "store":
                kwargs["queryset"] = db_field.related_model.objects.filter(id=request.active_store.id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
