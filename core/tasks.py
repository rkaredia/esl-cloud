import io
import time
import random
import logging
import sys
from celery import shared_task
from django.utils import timezone
from django.core.files.base import ContentFile
from django.core.cache import cache
from .models import ESLTag
from .utils import generate_esl_image
from .mqtt_client import mqtt_service

logger = logging.getLogger(__name__)

def log_info(message):
    logger.info(message)
    print(message, file=sys.stdout)

@shared_task(bind=True, name="core.tasks.update_tag_image_task")
def update_tag_image_task(self, tag_id):
    """
    STAGE 1: Generate and Store.
    Processes the pixel drawing and saves the BMP to storage.
    """
    # 1. Jitter to stagger concurrent starts (prevents race conditions on lock)
    time.sleep(random.uniform(0, 0.1))

    # 2. Atomic Lock check & set (30s timeout)
    # This is our primary defense against duplicate signal triggers
    lock_id = f"lock-tag-gen-{tag_id}"
    if not cache.add(lock_id, self.request.id, 30): 
        log_info(f"[STAGE 1] Aborting duplicate task for Tag {tag_id}. Lock already held.")
        return "Duplicate aborted"

    log_info(f"[STAGE 1 START] Processing Tag ID: {tag_id} | Task: {self.request.id}")
    
    try:
        # We use select_related to minimize DB queries during generation
        tag = ESLTag.objects.select_related('hardware_spec', 'paired_product').get(pk=tag_id)
        
        # Set status to Processing immediately
        tag.sync_state = 'PROCESSING'
        tag.save(update_fields=['sync_state'])

        if not tag.paired_product:
            log_info(f"[STAGE 1] Tag {tag.tag_mac} has no paired product. Reverting to IDLE.")
            tag.sync_state = 'IDLE'
            tag.save(update_fields=['sync_state'])
            cache.delete(lock_id)
            return "Skipped: No product"

        # 3. Pixel Generation
        try:
            log_info(f"[STAGE 1] Calling generate_esl_image for {tag.tag_mac}")
            pil_img = generate_esl_image(tag_id)
            if pil_img.mode != 'RGB':
                pil_img = pil_img.convert('RGB')
        except Exception as e:
            log_info(f"[STAGE 1 ERROR] Image gen failed for {tag.tag_mac}: {str(e)}")
            tag.sync_state = 'GEN_FAILED'
            tag.save(update_fields=['sync_state'])
            cache.delete(lock_id)
            return f"Generation Failed: {str(e)}"

        # 4. BMP Conversion
        buf = io.BytesIO()
        pil_img.save(buf, format='BMP')
        bmp_bytes = buf.getvalue()

        # 5. Save Image
        # Timestamp prevents browser cache issues in the admin preview
        filename = f"{tag.tag_mac.replace(':', '')}_{int(time.time())}.bmp"
        tag.tag_image.save(filename, ContentFile(bmp_bytes), save=False)
        
        tag.sync_state = 'IMAGE_READY'
        tag.last_image_gen_success = timezone.now()
        tag.last_image_task_id = self.request.id
        
        # We update specific fields to ensure the ESLTag post_save signal doesn't loop
        tag.save(update_fields=['tag_image', 'sync_state', 'last_image_gen_success', 'last_image_task_id'])
        
        log_info(f"[STAGE 1 SUCCESS] Image stored for {tag.tag_mac}. Dispatching Stage 2...")

        # 6. Dispatch to Stage 2 (MQTT Delivery)
        dispatch_tag_image_task.delay(tag_id)
        
        return f"BMP Generated for {tag.tag_mac}"

    except Exception as e:
        log_info(f"[STAGE 1 CRITICAL ERROR] {tag_id}: {str(e)}")
        ESLTag.objects.filter(pk=tag_id).update(sync_state='FAILED')
        cache.delete(lock_id)
        raise e

@shared_task(name="core.tasks.dispatch_tag_image_task")
def dispatch_tag_image_task(tag_id):
    """
    STAGE 2: MQTT Communication.
    Publishes the generated image to the Gateway.
    """
    lock_id = f"lock-tag-gen-{tag_id}"
    log_info(f"[STAGE 2 START] Dispatching Tag ID: {tag_id}")
    try:
        tag = ESLTag.objects.select_related('gateway').get(pk=tag_id)
        
        if not tag.gateway or not tag.gateway.estation_id:
            log_info(f"[STAGE 2 ERROR] Gateway ID missing for Tag {tag.tag_mac}")
            tag.sync_state = 'PUSH_FAILED'
            tag.save(update_fields=['sync_state'])
            cache.delete(lock_id)
            return "Gateway ID missing"

        if not tag.tag_image:
            log_info(f"[STAGE 2 ERROR] No image found for Tag {tag.tag_mac}")
            tag.sync_state = 'GEN_FAILED'
            tag.save(update_fields=['sync_state'])
            cache.delete(lock_id)
            return "No image to push"

        with tag.tag_image.open('rb') as f:
            image_bytes = f.read()

        token = random.randint(1, 255)
        log_info(f"[STAGE 2] Publishing to Gateway: {tag.gateway.estation_id} | Token: {token}")
        
        success = mqtt_service.publish_tag_update(
            tag.gateway.estation_id,
            tag.tag_mac,
            image_bytes,
            token
        )

        if success:
            tag.sync_state = 'PUSHED'
            tag.last_image_task_token = token
            tag.save(update_fields=['sync_state', 'last_image_task_token'])
            log_info(f"[STAGE 2 SUCCESS] {tag.tag_mac} sent to broker.")
            # Final clearance of the lock after the full pipeline is complete
            cache.delete(lock_id)
            return "MQTT Pushed"
        else:
            log_info(f"[STAGE 2 ERROR] MQTT Publish failed for {tag.tag_mac}")
            tag.sync_state = 'PUSH_FAILED'
            tag.save(update_fields=['sync_state'])
            cache.delete(lock_id)
            return "MQTT Failed"

    except Exception as e:
        log_info(f"[STAGE 2 CRITICAL ERROR] {tag_id}: {str(e)}")
        ESLTag.objects.filter(pk=tag_id).update(sync_state='PUSH_FAILED')
        cache.delete(lock_id)
        raise e