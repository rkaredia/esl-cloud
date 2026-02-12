import time
from celery import shared_task
from .models import ESLTag
from .utils import generate_esl_image  # Your existing image function



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
        
        return {
            "tag_id": tag_id,
            "status": "SUCCESS",
            "message": result_msg,
            "group_id": getattr(self.request, 'group', None) 
        }
    except ESLTag.DoesNotExist:
        return {"status": "FAILURE", "message": f"Tag ID {tag_id} not found."}