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

        # Bolt: Prefetch nested relationships to avoid N+1 queries during image storage path
        # calculation (upload_to) and model validation.
        # Prefetch everything needed for image generation AND storage path calculation
        tag = ESLTag.objects.select_related(
            'hardware_spec',
            'paired_product__preferred_supplier',
            'gateway__store__company'
        ).get(pk=tag_id)
        # Bolt: Use direct .update() to bypass heavy model validation (full_clean)
        # and redundant queries triggered by instance.save().

        # Use .update() for state changes to bypass full_clean() and redundant queries
        ESLTag.objects.filter(pk=tag_id).update(sync_state='PROCESSING')

        if not tag.paired_product:
            ESLTag.objects.filter(pk=tag_id).update(sync_state='IDLE')
            cache.delete(lock_id)
            return "Skipped: No product"

        # Image Generation
        try:
            pil_img = generate_esl_image(tag_id, tag_instance=tag)
            if pil_img.mode != 'RGB': pil_img = pil_img.convert('RGB')
        except Exception as e:
            logger.exception(f"Image gen failed for {tag.tag_mac}")
            ESLTag.objects.filter(pk=tag_id).update(sync_state='GEN_FAILED')
            cache.delete(lock_id)
            return f"Generation Failed: {str(e)}"

        # Save to Storage
        buf = io.BytesIO()
        pil_img.save(buf, format='BMP')
        filename = f"{tag.tag_mac.replace(':', '')}_{int(time.time())}.bmp"
        tag.tag_image.save(filename, ContentFile(buf.getvalue()), save=False)

        now = timezone.now()
        ESLTag.objects.filter(pk=tag_id).update(
            tag_image=tag.tag_image.name,
            sync_state='IMAGE_READY',
            last_image_gen_success=now,
            last_image_task_id=self.request.id
        )
        
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
            ESLTag.objects.filter(pk=tag_id).update(sync_state='PUSH_FAILED')
            cache.delete(lock_id)
            return "Gateway ID missing"

        if not tag.tag_image:
            ESLTag.objects.filter(pk=tag_id).update(sync_state='GEN_FAILED')
            cache.delete(lock_id)
            return "No image to push"

        with tag.tag_image.open('rb') as f:
            image_bytes = f.read()

        token = random.randint(1, 255)
        success = mqtt_service.publish_tag_update(tag.gateway.estation_id, tag.tag_mac, image_bytes, token)

        if success:
            ESLTag.objects.filter(pk=tag_id).update(sync_state='PUSHED', last_image_task_token=token)
            cache.delete(lock_id)
            return "MQTT Pushed"
        else:
            ESLTag.objects.filter(pk=tag_id).update(sync_state='PUSH_FAILED')
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
        # Bolt: Optimization - Use .values_list('id', flat=True) to avoid instantiating full model objects.
        # This significantly reduces memory usage and DB payload for stores with thousands of tags.
        # We use list() to evaluate the queryset once and then use len() to avoid a redundant COUNT query.
        tag_ids = list(ESLTag.objects.filter(
            gateway__store_id=store_id,
            paired_product__isnull=False
        ).values_list('id', flat=True))

        for tid in tag_ids:
            update_tag_image_task.delay(tid)

        return f"Queued {len(tag_ids)} tags for store {store_id}"
    except Exception as e:
        logger.exception(f"Error in refresh_store_products_task for store {store_id}")
        raise e
