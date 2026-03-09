from django.contrib import admin
from django.db import models
from ..managers import StoreManager

"""
DJANGO ADMIN MIXINS: MULTI-TENANCY & SECURITY
--------------------------------------------
In Django Admin, a 'Mixin' is a class that provides extra functionality
to multiple Admin classes.

The 'StoreFilteredAdmin' mixin is the heart of our Multi-Tenant security.
It ensures that a manager for 'Store A' NEVER sees products or tags
belonging to 'Store B', even if they try to manipulate the URL.

If you are coming from a Data Warehouse background, think of this as
'Row-Level Security' (RLS) or 'Fine-Grained Access Control' applied
at the application layer.
"""

class StoreFilteredAdmin(admin.ModelAdmin):
    """
    SAIS MULTI-TENANT FILTER
    ------------------------
    Automatically restricts every list view and dropdown menu in the
    Admin UI to ONLY show data belonging to the 'active_store'.
    """

    def get_queryset(self, request):
        """
        LIST VIEW FILTERING
        -------------------
        This method determines which rows are shown in the main table
        (the 'Change List') for a model.
        """
        # Start with the full list of records
        qs = super().get_queryset(request)

        # Get the store currently selected in the header dropdown
        active_store = getattr(request, 'active_store', None)

        if active_store:
            # Use our custom StoreManager (from managers.py) to filter the data
            if hasattr(self.model.objects, 'for_store'):
                return self.model.objects.for_store(active_store)

        # SECURITY FALLBACK: If no store is selected and user is NOT a superuser,
        # return an empty list (show nothing).
        return qs if request.user.is_superuser else qs.none()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        DROPDOWN FILTERING
        ------------------
        When you are editing an ESL Tag, this ensures that the 'Product'
        dropdown ONLY lists products belonging to the SAME store.
        """
        if not request.user.is_superuser and hasattr(request, 'active_store'):
            # If we are selecting a Gateway for a Tag:
            if db_field.name == "gateway":
                kwargs["queryset"] = db_field.related_model.objects.for_store(request.active_store)

            # If we are pairing a Product with a Tag:
            if db_field.name == "paired_product":
                kwargs["queryset"] = db_field.related_model.objects.for_store(request.active_store)

            # Ensure they can't change the Tag's store to one they don't own:
            if db_field.name == "store":
                kwargs["queryset"] = db_field.related_model.objects.filter(id=request.active_store.id)

        return super().formfield_for_foreignkey(db_field, request, **kwargs)
