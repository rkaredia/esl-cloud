from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from .models import Product

@receiver(post_save, sender=Product)
def update_tags_on_product_change(sender, instance, **kwargs):
    """
    When a product changes, trigger image updates for all related tags.
    """
    from core.tasks import update_tag_image_task
    
    # Use '.tags' because you defined related_name="tags" in your ESLTag model
    related_tags = instance.esl_tags.all()
    
    for tag in related_tags:
        # We use transaction.on_commit to ensure the Product save is 
        # finished before the worker starts reading the new price/name.
        transaction.on_commit(
            lambda t_id=tag.id: update_tag_image_task.delay(t_id)
        )