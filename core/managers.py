from django.db import models

class StoreManager(models.Manager):
    """
    Custom manager to strictly enforce store isolation.
    """
    def for_store(self, store):
        """Returns objects specifically for the given store."""
        if hasattr(self.model, 'store'):
            return self.get_queryset().filter(store=store)
        return self.get_queryset()
