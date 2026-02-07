from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Product, ESLTag
from .utils import generate_esl_image

@receiver(post_save, sender=Product)
def update_tag_image_on_product_change(sender, instance, **kwargs):
    # Find all tags paired with this product
    tags = ESLTag.objects.filter(paired_product=instance)
    for tag in tags:
        generate_esl_image(tag)