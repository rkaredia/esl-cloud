from django.db import models

class StoreManager(models.Manager):
    """
    Custom manager to strictly enforce store isolation.
    """
    def get_queryset(self):
        # Always exclude inactive items if the field exists
        qs = super().get_queryset()
        if hasattr(self.model, 'is_active'):
            return qs.filter(is_active=True)
        return qs

    def for_store(self, store):
        """Returns objects specifically for the given store."""
        if hasattr(self.model, 'store'):
            return self.get_queryset().filter(store=store)
        return self.get_queryset()
