from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from .models import Product, ESLTag
from django.core.cache import cache


@receiver(post_save, sender=Product)
def update_tags_on_product_change(sender, instance, **kwargs):
    """
    When a Product changes (Price, Name, etc.), trigger updates 
    for all ESL tags linked to this product.
    """
    from core.tasks import update_tag_image_task
    
    related_tags = instance.esl_tags.all()
    for tag in related_tags:
        # Atomic debounce for the specific tag
        debounce_key = f"signal_debounce_{tag.id}"
        # We only queue the task if the key doesn't exist (expires in 5s)
        if cache.add(debounce_key, "locked", timeout=5):
            transaction.on_commit(
                lambda t_id=tag.id: update_tag_image_task.delay(t_id)
            )

@receiver(post_save, sender=ESLTag)
def trigger_image_update_on_tag_save(sender, instance, **kwargs):
    """
    Triggers update when the Tag's own hardware or product pairing changes.
    """
    # 1. Check for Internal Updates
    # We ignore saves that are just updating the state/image to avoid loops
    update_fields = kwargs.get('update_fields')
    if update_fields is not None:
        trigger_fields = {'paired_product', 'gateway', 'hardware_spec'}
        if not any(field in update_fields for field in trigger_fields):
            return

    # 2. Atomic Debounce
    # This prevents the double-trigger from Admin (save_model + signal)
    debounce_key = f"signal_debounce_{instance.id}"
    if not cache.add(debounce_key, "locked", timeout=5):
        return

    # 3. Trigger Task
    if instance.paired_product and instance.hardware_spec:
        from core.tasks import update_tag_image_task
        transaction.on_commit(
            lambda: update_tag_image_task.delay(instance.id)
        )