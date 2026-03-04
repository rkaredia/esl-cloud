import io
import time
import random
import logging
import sys
from celery import shared_task
from django.utils import timezone
from django.core.files.base import ContentFile
from django.core.cache import cache
from .models import ESLTag, Store
from .utils import generate_esl_image
from .mqtt_client import mqtt_service

logger = logging.getLogger(__name__)

@shared_task(bind=True, name="core.tasks.update_tag_image_task")
def update_tag_image_task(self, tag_id):
    """
    STAGE 1: Generate and Store.
    Renders the ESL BMP image and saves it to the storage system.
    """
    try:
        time.sleep(random.uniform(0, 0.1))
        lock_id = f"lock-tag-gen-{tag_id}"
        if not cache.add(lock_id, self.request.id, 30):
            logger.info(f"Aborting duplicate task for Tag {tag_id}. Lock held.")
            return "Duplicate aborted"

        logger.debug(f"Processing Tag ID: {tag_id} | Task: {self.request.id}")

        tag = ESLTag.objects.select_related('hardware_spec', 'paired_product').get(pk=tag_id)
        tag.sync_state = 'PROCESSING'
        tag.save(update_fields=['sync_state'])

        if not tag.paired_product:
            tag.sync_state = 'IDLE'
            tag.save(update_fields=['sync_state'])
            cache.delete(lock_id)
            return "Skipped: No product"

        # Image Generation
        try:
            pil_img = generate_esl_image(tag_id)
            if pil_img.mode != 'RGB': pil_img = pil_img.convert('RGB')
        except Exception as e:
            logger.exception(f"Image gen failed for {tag.tag_mac}")
            tag.sync_state = 'GEN_FAILED'
            tag.save(update_fields=['sync_state'])
            cache.delete(lock_id)
            return f"Generation Failed: {str(e)}"

        # Save to Storage
        buf = io.BytesIO()
        pil_img.save(buf, format='BMP')
        filename = f"{tag.tag_mac.replace(':', '')}_{int(time.time())}.bmp"
        tag.tag_image.save(filename, ContentFile(buf.getvalue()), save=False)
        tag.sync_state, tag.last_image_gen_success, tag.last_image_task_id = 'IMAGE_READY', timezone.now(), self.request.id
        tag.save(update_fields=['tag_image', 'sync_state', 'last_image_gen_success', 'last_image_task_id'])
        
        dispatch_tag_image_task.delay(tag_id)
        return f"BMP Generated for {tag.tag_mac}"

    except Exception as e:
        logger.exception(f"Critical error in update_tag_image_task for tag {tag_id}")
        ESLTag.objects.filter(pk=tag_id).update(sync_state='FAILED')
        if 'lock_id' in locals(): cache.delete(lock_id)
        raise e

@shared_task(name="core.tasks.dispatch_tag_image_task")
def dispatch_tag_image_task(tag_id):
    """
    STAGE 2: MQTT Communication.
    Publishes the generated image to the physical ESL gateway.
    """
    lock_id = f"lock-tag-gen-{tag_id}"
    try:
        tag = ESLTag.objects.select_related('gateway').get(pk=tag_id)
        
        if not tag.gateway or not tag.gateway.estation_id:
            tag.sync_state = 'PUSH_FAILED'
            tag.save(update_fields=['sync_state'])
            cache.delete(lock_id)
            return "Gateway ID missing"

        if not tag.tag_image:
            tag.sync_state = 'GEN_FAILED'
            tag.save(update_fields=['sync_state'])
            cache.delete(lock_id)
            return "No image to push"

        with tag.tag_image.open('rb') as f:
            image_bytes = f.read()

        token = random.randint(1, 255)
        success = mqtt_service.publish_tag_update(tag.gateway.estation_id, tag.tag_mac, image_bytes, token)

        if success:
            tag.sync_state, tag.last_image_task_token = 'PUSHED', token
            tag.save(update_fields=['sync_state', 'last_image_task_token'])
            cache.delete(lock_id)
            return "MQTT Pushed"
        else:
            tag.sync_state = 'PUSH_FAILED'
            tag.save(update_fields=['sync_state'])
            cache.delete(lock_id)
            return "MQTT Failed"

    except Exception as e:
        logger.exception(f"Critical error in dispatch_tag_image_task for tag {tag_id}")
        ESLTag.objects.filter(pk=tag_id).update(sync_state='PUSH_FAILED')
        cache.delete(lock_id)
        raise e

@shared_task(name="core.tasks.refresh_store_products_task")
def refresh_store_products_task(store_id):
    """Refreshes all tags for a specific store."""
    try:
        tags = ESLTag.objects.filter(gateway__store_id=store_id, paired_product__isnull=False)
        for tag in tags:
            update_tag_image_task.delay(tag.id)
        return f"Queued {tags.count()} tags for store {store_id}"
    except Exception as e:
        logger.exception(f"Error in refresh_store_products_task for store {store_id}")
        raise e
