from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from .models import Product, ESLTag
from django.core.cache import cache

"""
DJANGO SIGNALS: THE EVENT SYSTEM
--------------------------------
Signals allow certain senders to notify a set of receivers that
some action has taken place.

In SAIS, we use 'post_save' signals to achieve 'Event-Driven' updates:
"When X happens, automatically do Y."

Scenario:
- User saves a new PRICE for a Product in the Admin.
- Django fires a 'post_save' signal.
- The 'update_tags_on_product_change' receiver catches it.
- It finds all ESL Tags linked to that product and queues a Celery task.
- The physical price tag updates automatically!
"""

@receiver(post_save, sender=Product)
def update_tags_on_product_change(sender, instance, **kwargs):
    """
    EVENT: PRODUCT MODIFIED
    -----------------------
    When a Product changes (Price, Name, etc.), trigger updates 
    for all ESL tags linked to this product.
    """
    # Performance: Use model-level change detection to skip unnecessary processing
    if not getattr(instance, '_needs_refresh', True):
        return

    from .utils import trigger_bulk_sync
    
    # 1. Look up all Tags currently displaying this product
    tag_ids = list(instance.esl_tags.values_list('id', flat=True))

    if tag_ids:
        # Performance: Use trigger_bulk_sync to batch dispatch tasks in one transaction hook.
        # This also removes the O(N) cache.add (Redis) overhead from the signal handler.
        transaction.on_commit(
            lambda: trigger_bulk_sync(tag_ids)
        )

@receiver(post_save, sender=ESLTag)
def trigger_image_update_on_tag_save(sender, instance, **kwargs):
    """
    EVENT: TAG LINKAGE MODIFIED
    ---------------------------
    Triggers an update when the Tag itself is changed (e.g., paired
    with a different product or switched to a new Template).
    """
    # Performance: Use model-level change detection. This handles both 'update_fields' saves
    # and full-object saves (common in Django Admin) without redundant task triggering.
    if not getattr(instance, '_needs_refresh', True):
        return

    # 1. Debouncing: Prevents triggering multiple tasks for the same tag in rapid succession
    debounce_key = f"signal_debounce_{instance.id}"
    if not cache.add(debounce_key, "locked", timeout=5):
        return

    # 2. Trigger Task if the tag has enough info to render
    if instance.paired_product and instance.hardware_spec:
        from core.tasks import update_tag_image_task
        transaction.on_commit(
            lambda: update_tag_image_task.delay(instance.id)
        )
