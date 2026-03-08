import io
import os
import time
import random
import logging
import sys
from celery import shared_task
from django.utils import timezone
from django.core.files.base import ContentFile
from django.core.cache import cache
from .models import ESLTag, Store, Gateway, GlobalSetting, MQTTMessage
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
        
        # Determine which gateway ID to use:
        # 1. Hard-linked gateway's estation_id
        # 2. last_successful_gateway_id
        target_gateway_id = None
        if tag.gateway and tag.gateway.estation_id:
            target_gateway_id = tag.gateway.estation_id
        elif tag.last_successful_gateway_id:
            target_gateway_id = tag.last_successful_gateway_id

        if not target_gateway_id:
            ESLTag.objects.filter(pk=tag_id).update(sync_state='PUSH_FAILED')
            cache.delete(lock_id)
            return "No target gateway identified"

        if not tag.tag_image:
            ESLTag.objects.filter(pk=tag_id).update(sync_state='GEN_FAILED')
            cache.delete(lock_id)
            return "No image to push"

        with tag.tag_image.open('rb') as f:
            image_bytes = f.read()

        token = random.randint(1, 255)
        success = mqtt_service.publish_tag_update(target_gateway_id, tag.tag_mac, image_bytes, token)

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

@shared_task(name="core.tasks.check_gateways_status_task")
def check_gateways_status_task():
    """
    Checks all gateways and marks them offline if they haven't sent a heartbeat within the timeout window.
    Runs every minute (configured in Celery Beat).
    """
    try:
        # Get settings or defaults
        default_interval = int(GlobalSetting.objects.filter(key='DEFAULT_HEARTBEAT_INTERVAL').values_list('value', flat=True).first() or 300)
        multiplier = int(GlobalSetting.objects.filter(key='OFFLINE_TIMEOUT_MULTIPLIER').values_list('value', flat=True).first() or 4)

        gateways = Gateway.objects.filter(is_online=True)
        count_offline = 0
        now = timezone.now()

        for gw in gateways:
            interval = gw.heartbeat_interval or default_interval
            timeout_seconds = interval * multiplier

            last_signal = gw.last_heartbeat or gw.created_at
            if (now - last_signal).total_seconds() > timeout_seconds:
                gw.is_online = False
                gw.save(update_fields=['is_online'])
                count_offline += 1
                logger.info(f"Gateway {gw.estation_id} marked OFFLINE (No heartbeat for {timeout_seconds}s)")

        return f"Checked status. Marked {count_offline} gateways offline."
    except Exception:
        logger.exception("Error in check_gateways_status_task")
        return "Status check failed"

@shared_task(name="core.tasks.cleanup_old_logs_task")
def cleanup_old_logs_task():
    """
    Deletes MQTT logs (files and DB) and system logs older than retention period.
    """
    try:
        from django.conf import settings
        import time

        # 1. Database Cleanup
        retention_days = int(GlobalSetting.objects.filter(key='LOG_RETENTION_DAYS').values_list('value', flat=True).first() or 15)
        cutoff = timezone.now() - timezone.timedelta(days=retention_days)
        db_count, _ = MQTTMessage.objects.filter(timestamp__lt=cutoff).delete()

        # 2. File Cleanup
        log_dirs = [
            os.path.join(settings.BASE_DIR, 'logs', 'mqtt', 'received'),
            os.path.join(settings.BASE_DIR, 'logs', 'mqtt', 'sent'),
            os.path.join(settings.BASE_DIR, 'logs', 'mqtt'),
            os.path.join(settings.BASE_DIR, 'logs'),
        ]

        now = time.time()
        count_deleted = 0

        for directory in log_dirs:
            if not os.path.exists(directory): continue

            for f in os.listdir(directory):
                filepath = os.path.join(directory, f)
                if not os.path.isfile(filepath): continue

                # Check age
                if os.stat(filepath).st_mtime < now - (retention_days * 86400):
                    os.remove(filepath)
                    count_deleted += 1

        return f"Cleaned up {db_count} DB records and {count_deleted} log files."
    except Exception:
        logger.exception("Error in cleanup_old_logs_task")
        return "Cleanup failed"
