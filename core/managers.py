from django.db import models

"""
DJANGO MANAGERS & DATA ISOLATION
--------------------------------
In Django, a 'Manager' is the interface through which database query
operations are provided to Django models. At least one Manager exists
for every model in Django (the default is called 'objects').

Custom Managers allow us to:
1. Define common filters (like 'Only Active Records') that apply automatically.
2. Add custom 'Table-level' methods (like 'for_store(xyz)').
3. Enforce Multi-Tenancy (Data Isolation) at the lowest possible layer.

Think of a Manager as a 'Stored Procedure' or a 'View' definition in a
traditional database that ensures you only ever see the data you are
supposed to see.
"""

class StoreManager(models.Manager):
    """
    SAIS MULTI-TENANT ISOLATION MANAGER
    -----------------------------------
    This manager is used by models like Product, Gateway, and ESLTag
    to ensure that they are automatically filtered by 'Active' status
    and provide easy helpers for Store-level isolation.
    """

    def get_queryset(self):
        """
        EDUCATIONAL: get_queryset() is the base method for every query
        (like Model.objects.all()). By overriding it, we can inject
        default filters that apply to EVERY query.
        """
        # Start with the standard list of all records
        qs = super().get_queryset()

        # BUSINESS RULE: Never show 'Inactive' (soft-deleted) records in the UI.
        # We check if the model actually has an 'is_active' field first.
        if hasattr(self.model, 'is_active'):
            return qs.filter(is_active=True)

        return qs

    def for_store(self, store):
        """
        QUERY HELPER: Returns objects specifically for the given store.
        Usage: Product.objects.for_store(my_store_obj)
        """
        # Ensure we only try to filter if the model actually has a 'store' relationship.
        if hasattr(self.model, 'store'):
            return self.get_queryset().filter(store=store)

        return self.get_queryset()
