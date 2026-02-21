import time
from celery import shared_task
from .models import ESLTag
from .utils import generate_esl_image  # Your existing image function
from django.utils import timezone


@shared_task
def add_test_task(x, y):
    # This is just a test to make sure things are working!
    time.sleep(5) # Simulate a heavy job
    return x + y        

@shared_task(bind=True, name="core.tasks.update_tag_image_task", ignore_result=False)
def update_tag_image_task(self, tag_id):
    from .models import ESLTag
    try:
        # Fetch with select_related for efficiency
        tag = ESLTag.objects.select_related('hardware_spec', 'paired_product').get(pk=tag_id)
        
        # --- THE LOGIC YOU WERE LOOKING FOR ---
        if not tag.paired_product:
            return {"status": "SKIPPED", "tag": tag.tag_mac, "message": "No product paired."}
            
        if not tag.hardware_spec:
            return {"status": "SKIPPED", "tag": tag.tag_mac, "message": "Missing hardware spec."}
        # --------------------------------------

        # Proceed to image generation
        result_msg = generate_esl_image(tag_id)
        tag.last_image_gen_success = timezone.now()
        tag.last_image_task_id = self.request.id
        tag.save()
        return {
            "tag_id": tag_id,
            "status": "SUCCESS",
            "message": result_msg,
            "group_id": getattr(self.request, 'group', None) 
        }
    except ESLTag.DoesNotExist:
        return {"status": "FAILURE", "message": f"Tag ID {tag_id} not found."}


@shared_task(name="core.tasks.refresh_store_products_task")
def refresh_store_products_task(store_id):
    """
    Finds all tags in a store and triggers image refresh.
    Uses throttling (sleep) to protect Redis/Message Broker.
    """
    # Query IDs for tags belonging to this store via the Gateway
    tag_ids = ESLTag.objects.filter(
        gateway__store_id=store_id
    ).values_list('id', flat=True).iterator()

    count = 0
    for tag_id in tag_ids:
        update_tag_image_task.apply_async(args=[tag_id])
        count += 1
        
        # Throttle: 20 tasks per second
        # Prevents Redis 'Memory Limit Exceeded' or 'Connection Timeout'
        if count % 10 == 0:
            time.sleep(0.05)
            
    return f"Successfully throttled and queued {count} tags for Store {store_id}"