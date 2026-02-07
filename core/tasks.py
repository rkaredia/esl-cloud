import time
from celery import shared_task
from .models import ESLTag
from .utils import generate_esl_image  # Your existing image function

@shared_task

def update_tag_image_task(tag_id):
    # select_related avoids the error by fetching the hardware spec and product in one go
    tag = ESLTag.objects.select_related('hardware_spec', 'paired_product').get(pk=tag_id)
    
    if not tag.paired_product or not tag.hardware_spec:
        return "Missing product or hardware spec"

    # Use the new fields!
    width = tag.hardware_spec.width_px
    height = tag.hardware_spec.height_px
    is_special = tag.paired_product.is_on_special
    """
    Background task to generate and save a tag image.
    """
    try:
        tag = ESLTag.objects.get(pk=tag_id)
        # This calls your existing logic but inside the worker
        generate_esl_image(tag)
        return f"Successfully updated image for Tag: {tag.tag_mac}"
    except ESLTag.DoesNotExist:
        return f"Tag {tag_id} not found"    


@shared_task
def add_test_task(x, y):
    # This is just a test to make sure things are working!
    time.sleep(5) # Simulate a heavy job
    return x + y        

