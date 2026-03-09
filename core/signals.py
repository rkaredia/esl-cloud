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
    from core.tasks import update_tag_image_task
    
    # 1. Look up all Tags currently displaying this product
    tag_ids = list(instance.esl_tags.values_list('id', flat=True))

    for t_id in tag_ids:
        # 2. DEBOUNCING:
        # Prevents triggering the same update multiple times in a row
        # (e.g. if the user clicks 'Save' twice quickly).
        debounce_key = f"signal_debounce_{t_id}"

        # cache.add only returns True if the key didn't exist before.
        # This acts as a 5-second lock.
        if cache.add(debounce_key, "locked", timeout=5):
            # 3. ON COMMIT:
            # We only queue the background task if the database successfully
            # writes the product change.
            transaction.on_commit(
                lambda current_tid=t_id: update_tag_image_task.delay(current_tid)
            )

@receiver(post_save, sender=ESLTag)
def trigger_image_update_on_tag_save(sender, instance, **kwargs):
    """
    EVENT: TAG LINKAGE MODIFIED
    ---------------------------
    Triggers an update when the Tag itself is changed (e.g., paired
    with a different product or switched to a new Template).
    """

    # 1. PREVENT INFINITE LOOPS:
    # The 'update_tag_image_task' saves the BMP image back to the Tag model.
    # We must IGNORE that specific save, otherwise we would trigger
    # another task, which would save again, forever.
    update_fields = kwargs.get('update_fields')
    if update_fields is not None:
        # Only trigger if these SPECIFIC fields were edited
        trigger_fields = {'paired_product', 'gateway', 'hardware_spec','template_id'}
        if not any(field in update_fields for field in trigger_fields):
            return

    # 2. Debouncing (Same logic as above)
    debounce_key = f"signal_debounce_{instance.id}"
    if not cache.add(debounce_key, "locked", timeout=5):
        return

    # 3. Trigger Task if the tag has enough info to render
    if instance.paired_product and instance.hardware_spec:
        from core.tasks import update_tag_image_task
        transaction.on_commit(
            lambda: update_tag_image_task.delay(instance.id)
        )
