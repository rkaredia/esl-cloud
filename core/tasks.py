import io
import time
import random
import logging
from celery import shared_task
from django.utils import timezone
from PIL import Image
from .models import ESLTag
from .utils import generate_esl_image  # Your existing drawing logic
from .mqtt_client import mqtt_service   # The MQTT v5 / MsgPack handler

logger = logging.getLogger(__name__)

@shared_task
def add_test_task(x, y):
    # This is just a test to make sure things are working!
    time.sleep(5) # Simulate a heavy job
    return x + y        

@shared_task(bind=True, name="core.tasks.update_tag_image_task", ignore_result=False)
def update_tag_image_task(self, tag_id):
    """
    Orchestrates the full ESL update flow:
    Validation -> Image Generation -> MQTT Publish -> State Tracking
    """
    try:
        # Fetch with select_related for efficiency
        tag = ESLTag.objects.select_related('hardware_spec', 'paired_product', 'gateway').get(pk=tag_id)
        
        # 1. Validation Logic
        if not tag.paired_product:
            tag.sync_state = 'IDLE'
            tag.save()
            return {"status": "SKIPPED", "tag": tag.tag_mac, "message": "No product paired."}
            
        if not tag.hardware_spec:
            return {"status": "SKIPPED", "tag": tag.tag_mac, "message": "Missing hardware spec."}

        # 2. Update state to Pending
        tag.sync_state = 'PENDING'
        tag.save()

        # 3. Generate Image (Using your utility)
        # result_msg should ideally return the raw bytes or path to the generated image
        # For this flow, we generate the image and convert to BMP for the eStation
        image_data = generate_esl_image(tag_id) 
        
        # Ensure image is in 1-bit BMP format if generate_esl_image returns a PIL object
        # If generate_esl_image already returns bytes, skip this conversion
        if isinstance(image_data, Image.Image):
            buf = io.BytesIO()
            image_data.convert('1').save(buf, format='BMP')
            image_bytes = buf.getvalue()
        else:
            image_bytes = image_data

        # 4. Token Generation (1-255 for Task Result matching)
        token = random.randint(1, 255)
        
        # 5. Publish to MQTT via Service
        # Targets the specific eStation ID mapped to the gateway
        mqtt_service.publish_tag_update(
            gateway_id=tag.gateway.estation_id,
            tag_mac=tag.tag_mac,
            image_bytes=image_bytes,
            token=token
        )

        # 6. Record metadata for closed-loop confirmation
        tag.last_image_gen_success = timezone.now()
        tag.last_image_task_id = self.request.id
        tag.last_image_task_token = token
        tag.save()

        logger.info(f"Published update for Tag {tag.tag_mac} with Token {token}")
        
        return {
            "tag_id": tag_id,
            "status": "SUCCESS",
            "token": token,
            "message": f"Queued for eStation {tag.gateway.estation_id}",
            "group_id": getattr(self.request, 'group', None) 
        }

    except ESLTag.DoesNotExist:
        return {"status": "FAILURE", "message": f"Tag ID {tag_id} not found."}
    except Exception as e:
        if 'tag' in locals():
            tag.sync_state = 'FAILED'
            tag.save()
        logger.error(f"Error in update_tag_image_task: {str(e)}")
        raise e


@shared_task(name="core.tasks.refresh_store_products_task")
def refresh_store_products_task(store_id):
    """
    Finds all tags in a store and triggers image refresh.
    Uses throttling (20 tasks/sec) to protect MQTT broker and eStation queue.
    """
    tag_ids = ESLTag.objects.filter(
        gateway__store_id=store_id
    ).values_list('id', flat=True).iterator()

    count = 0
    for tag_id in tag_ids:
        update_tag_image_task.apply_async(args=[tag_id])
        count += 1
        
        # Throttle logic: protects against burst loads
        if count % 10 == 0:
            time.sleep(0.05) # 0.05s * 2 = 0.1s per 20 tags
            
    return f"Successfully queued {count} tags for Store {store_id} with throttling."

  #Admin Save -> Signal -> Celery Task -> Image Gen -> MQTT Publish (with Token) -> Wait for MQTT Result -> Update sync_state.  