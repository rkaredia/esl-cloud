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

"""
CELERY BACKGROUND TASKS
-----------------------
In SAIS, expensive or time-consuming operations (like generating images
or talking to hardware) are moved to the 'Background'.

Why use Celery?
- If a user updates a price, the website shouldn't "hang" while waiting
  for an image to be generated and sent to a physical tag.
- Tasks are added to a 'Queue' (Redis) and processed by 'Workers'.
- This allows the system to handle thousands of updates simultaneously
  without slowing down the Admin UI.
"""

logger = logging.getLogger(__name__)

@shared_task(bind=True, name="core.tasks.update_tag_image_task")
def update_tag_image_task(self, tag_id):
    """
    STAGE 1: IMAGE GENERATION
    -------------------------
    This task creates the physical BMP file that will be displayed on the tag.

    EDUCATIONAL: 'shared_task' makes this function available to Celery.
    'bind=True' gives us access to 'self' (the task instance).
    """
    try:
        # Small random delay to prevent 'Thundering Herd' (too many tasks hitting
        # the DB at the exact same microsecond during bulk imports).
        time.sleep(random.uniform(0, 0.1))

        # DISTRIBUTED LOCKING:
        # Prevents two workers from generating the same image at the same time.
        lock_id = f"lock-tag-gen-{tag_id}"
        if not cache.add(lock_id, self.request.id, 30):
            logger.info(f"Aborting duplicate task for Tag {tag_id}. Lock held.")
            return "Duplicate aborted"

        logger.debug(f"Processing Tag ID: {tag_id} | Task: {self.request.id}")

        # DATA PREFETCHING:
        # select_related performs a SQL JOIN to get related data (Company, Store, Product)
        # in a single query rather than multiple individual queries.
        tag = ESLTag.objects.select_related(
            'hardware_spec',
            'paired_product__preferred_supplier',
            'gateway__store__company'
        ).get(pk=tag_id)

        # Update status to 'PROCESSING' in the DB
        ESLTag.objects.filter(pk=tag_id).update(sync_state='PROCESSING')

        if not tag.paired_product:
            ESLTag.objects.filter(pk=tag_id).update(sync_state='IDLE')
            cache.delete(lock_id)
            return "Skipped: No product"

        # CALL UTILS: Render the actual BMP image using Pillow (PIL)
        try:
            pil_img = generate_esl_image(tag_id, tag_instance=tag)
        except Exception as e:
            logger.exception(f"Image gen failed for {tag.tag_mac}")
            ESLTag.objects.filter(pk=tag_id).update(sync_state='GEN_FAILED')
            cache.delete(lock_id)
            return f"Generation Failed: {str(e)}"

        # SAVE TO DISK:
        # Write the image to a memory buffer, then save it to Django's storage system.
        buf = io.BytesIO()
        pil_img.save(buf, format='BMP')
        filename = f"{tag.tag_mac.replace(':', '')}_{int(time.time())}.bmp"
        tag.tag_image.save(filename, ContentFile(buf.getvalue()), save=False)

        # UPDATE DB: Record that the image is ready for delivery.
        now = timezone.now()
        ESLTag.objects.filter(pk=tag_id).update(
            tag_image=tag.tag_image.name,
            sync_state='IMAGE_READY',
            last_image_gen_success=now,
            last_image_task_id=self.request.id
        )
        
        # CHAINING: Trigger the next stage (MQTT Delivery)
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
    STAGE 2: HARDWARE PUSH (MQTT)
    -----------------------------
    Takes the BMP generated in Stage 1 and sends it to the physical gateway.
    Includes 'Failover' logic to try multiple gateways if one is offline.
    """
    lock_id = f"lock-tag-gen-{tag_id}"
    try:
        tag = ESLTag.objects.select_related('gateway', 'store').get(pk=tag_id)
        
        if not tag.tag_image:
            ESLTag.objects.filter(pk=tag_id).update(sync_state='GEN_FAILED')
            cache.delete(lock_id)
            return "No image to push"

        # FAILOVER ROTATION STRATEGY:
        # 1. Try the gateway that succeeded last time.
        # 2. Try the gateway currently assigned in the Admin.
        # 3. Try ANY other online gateway in the same store.
        gateways_to_try = []

        if tag.last_successful_gateway_id:
            gateways_to_try.append(tag.last_successful_gateway_id)

        if tag.gateway and tag.gateway.estation_id and tag.gateway.estation_id not in gateways_to_try:
            gateways_to_try.append(tag.gateway.estation_id)

        online_gateways = list(Gateway.objects.filter(
            store=tag.store,
            is_online=True
        ).exclude(
            estation_id__in=gateways_to_try
        ).values_list('estation_id', flat=True))

        gateways_to_try.extend(online_gateways)

        if not gateways_to_try:
            ESLTag.objects.filter(pk=tag_id).update(sync_state='PUSH_FAILED')
            cache.delete(lock_id)
            return "No online gateways found for store"

        # READ BMP: Load the file from disk into memory
        with tag.tag_image.open('rb') as f:
            image_bytes = f.read()

        # Generate a unique 'Token' for this specific hardware transaction.
        # The gateway will send this back in the result so we know WHICH update finished.
        token = random.randint(1, 255)

        # DELIVERY LOOP: Try each gateway until one accepts the message.
        for gateway_id in gateways_to_try:
            logger.info(f"Attempting update for {tag.tag_mac} via gateway {gateway_id}")
            success = mqtt_service.publish_tag_update(gateway_id, tag.tag_mac, image_bytes, token)

            if success:
                # MARK AS PUSHED: We now wait for the '/result' MQTT message to mark it SUCCESS.
                ESLTag.objects.filter(pk=tag_id).update(sync_state='PUSHED', last_image_task_token=token)
                cache.delete(lock_id)
                return f"MQTT Pushed via {gateway_id}"
            else:
                logger.warning(f"Failed to push to gateway {gateway_id} for tag {tag.tag_mac}")

        # All attempts failed
        ESLTag.objects.filter(pk=tag_id).update(sync_state='PUSH_FAILED')
        cache.delete(lock_id)
        return "MQTT Failed on all available gateways"

    except Exception as e:
        logger.exception(f"Critical error in dispatch_tag_image_task for tag {tag_id}")
        ESLTag.objects.filter(pk=tag_id).update(sync_state='PUSH_FAILED')
        cache.delete(lock_id)
        raise e

@shared_task(name="core.tasks.refresh_store_products_task")
def refresh_store_products_task(store_id):
    """
    BULK REFRESH
    ------------
    Queues an update for EVERY tag in a specific store.
    Useful after a template change or a bulk import.
    """
    try:
        # Use .values_list('id') to get only IDs (very fast, low memory).
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
    HEALTH MONITORING (Heartbeat Check)
    -----------------------------------
    Runs every minute via Celery Beat (Scheduled Task).
    If a gateway hasn't sent a heartbeat in X minutes, mark it 'Offline'.
    """
    try:
        # Load timeout thresholds from Global Settings
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
    DATA HOUSEKEEPING
    -----------------
    Deletes old MQTT messages and log files to keep the database
    and disk from filling up. Retention is usually 15-30 days.
    """
    try:
        from django.conf import settings
        import time

        # 1. Database Purge
        retention_days = int(GlobalSetting.objects.filter(key='LOG_RETENTION_DAYS').values_list('value', flat=True).first() or 15)
        cutoff = timezone.now() - timezone.timedelta(days=retention_days)
        db_count, _ = MQTTMessage.objects.filter(timestamp__lt=cutoff).delete()

        # 2. File Purge
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

                # Delete files older than retention_days
                if os.stat(filepath).st_mtime < now - (retention_days * 86400):
                    os.remove(filepath)
                    count_deleted += 1

        return f"Cleaned up {db_count} DB records and {count_deleted} log files."
    except Exception:
        logger.exception("Error in cleanup_old_logs_task")
        return "Cleanup failed"
