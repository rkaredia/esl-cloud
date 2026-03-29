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
def update_tag_image_task(self, tag_id, is_retry=False):
    """
    STAGE 1: IMAGE GENERATION
    -------------------------
    This task creates the physical BMP file that will be displayed on the tag.
    """
    try:
        # Small random delay to prevent 'Thundering Herd'
        time.sleep(random.uniform(0, 0.1))

        # DISTRIBUTED LOCKING
        lock_id = f"lock-tag-gen-{tag_id}"
        if not cache.add(lock_id, self.request.id, 30):
            logger.info(f"Aborting duplicate task for Tag {tag_id}. Lock held.")
            return "Duplicate aborted"

        logger.debug(f"Processing Tag ID: {tag_id} | Task: {self.request.id} | Retry: {is_retry}")

        # Reset retry count and generate a base token if this is a fresh update
        if not is_retry:
            base_token = random.randint(0, 16383)
            ESLTag.objects.filter(pk=tag_id).update(retry_count=0, last_image_task_token=base_token, sync_state='PROCESSING')
        else:
            ESLTag.objects.filter(pk=tag_id).update(sync_state='PROCESSING')

        # DATA PREFETCHING
        tag = ESLTag.objects.select_related(
            'hardware_spec',
            'paired_product__preferred_supplier',
            'gateway__store__company'
        ).get(pk=tag_id)

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

def trigger_gateway_processing(gateway_id):
    """Ensures a worker is processing the queue for this gateway."""
    lock_key = f"gateway_proc_lock_{gateway_id}"
    # Use a specific value to identify the active task
    if cache.add(lock_key, "active", 60):
        logger.info(f"Triggering new queue processor for gateway {gateway_id}")
        process_gateway_queue_task.delay(gateway_id)
    else:
        logger.debug(f"Queue processor already active for gateway {gateway_id}")

@shared_task(name="core.tasks.dispatch_tag_image_task")
def dispatch_tag_image_task(tag_id):
    """
    STAGE 2: GATEWAY ASSIGNMENT
    ---------------------------
    Decides which gateway will handle this tag and triggers the queue.
    """
    lock_id = f"lock-tag-gen-{tag_id}"
    try:
        tag = ESLTag.objects.select_related('gateway', 'store').get(pk=tag_id)
        
        if not tag.tag_image:
            ESLTag.objects.filter(pk=tag_id).update(sync_state='GEN_FAILED')
            cache.delete(lock_id)
            return "No image to push"

        # FAILOVER ROTATION STRATEGY
        gateways_to_try = []
        if tag.last_successful_gateway_id:
            gateways_to_try.append(tag.last_successful_gateway_id)

        if tag.gateway and tag.gateway.estation_id and tag.gateway.estation_id not in gateways_to_try:
            gateways_to_try.append(tag.gateway.estation_id)

        online_gateways = list(Gateway.objects.filter(
            store=tag.store
        ).exclude(
            is_online='OFFLINE'
        ).exclude(
            estation_id__in=gateways_to_try
        ).values_list('estation_id', flat=True))

        gateways_to_try.extend(online_gateways)

        if not gateways_to_try:
            cache.delete(lock_id)
            handle_tag_failure_task.delay(tag_id)
            return "No online gateways found for store"

        # Assign the first available gateway and trigger processing
        best_gateway_id = gateways_to_try[0]
        gateway_obj = Gateway.objects.filter(estation_id=best_gateway_id).first()

        ESLTag.objects.filter(pk=tag_id).update(
            gateway=gateway_obj,
            sync_state='IMAGE_READY'
        )

        trigger_gateway_processing(best_gateway_id)
        cache.delete(lock_id)
        return f"Queued for gateway {best_gateway_id}"

    except Exception:
        logger.exception(f"Error in dispatch_tag_image_task for tag {tag_id}")
        cache.delete(lock_id)
        handle_tag_failure_task.delay(tag_id)
        return "Dispatch Failed"

@shared_task(name="core.tasks.process_gateway_queue_task")
def process_gateway_queue_task(gateway_id):
    """
    STAGE 3: SERIALIZED DELIVERY
    ----------------------------
    Processes tags for a specific gateway one by one with a dynamic delay.
    Ensures strict serialization and one-at-a-time delivery.
    """
    from django.db import transaction
    lock_key = f"gateway_proc_lock_{gateway_id}"

    # 1. Maintain the lock to prevent other workers from starting a parallel loop.
    # TTL of 60 seconds is ample.
    if not cache.set(lock_key, "active", 60):
        # We assume the current process set it if it returns False?
        # Actually cache.set in Django returns None or True depending on implementation.
        # Let's just use .add and .set properly.
        pass

    # Ensure the lock is held
    cache.set(lock_key, "active", 60)

    # 2. Dynamic Settings
    delay_ms = int(GlobalSetting.objects.filter(key='ESL_SEND_DELAY_MS').values_list('value', flat=True).first() or 500)
    delay_seconds = max(0.1, delay_ms / 1000.0)

    # PROCESS BATCH: We process tags in a loop for 45 seconds max per task
    # to maintain high precision (avoiding Celery scheduling overhead for small delays).
    start_time = time.time()
    tags_processed_count = 0

    while (time.time() - start_time) < 45:
        # Extend the lock periodically
        cache.set(lock_key, "active", 60)

        tag = None
        # 3. Find and LOCK the next tag in the queue for THIS gateway
        with transaction.atomic():
            tag = ESLTag.objects.select_for_update(skip_locked=True).filter(
                gateway__estation_id=gateway_id,
                sync_state='IMAGE_READY'
            ).order_by('updated_at').first()

            if not tag:
                logger.info(f"Queue for gateway {gateway_id} is empty. (Processed: {tags_processed_count})")
                cache.delete(lock_key)
                return f"Queue empty. Processed: {tags_processed_count}"

            # Mark as 'PROCESSING' immediately to claim it
            ESLTag.objects.filter(pk=tag.pk).update(sync_state='PROCESSING')

        # 4. Prepare and Send
        try:
            # Re-fetch tag to ensure we have the latest state
            tag.refresh_from_db()
            tag_mac = tag.tag_mac.upper()

            if not tag.tag_image:
                 logger.error(f"Tag {tag.tag_mac} in queue has no image.")
                 ESLTag.objects.filter(pk=tag.pk).update(sync_state='GEN_FAILED')
            else:
                with tag.tag_image.open('rb') as f:
                    image_bytes = f.read()

                # TOKEN LOGIC: 2 bits for retry, 14 bits for unique ID
                base_token = (tag.last_image_task_token or 0) & 0x3FFF
                token = ((tag.retry_count & 0x03) << 14) | base_token

                logger.info(f"Pushing tag {tag_mac} to {gateway_id} | Token: {token} | Retry: {tag.retry_count}")

                success = mqtt_service.publish_tag_update(gateway_id, tag_mac, image_bytes, token)

                if success:
                    ESLTag.objects.filter(pk=tag.pk).update(
                        sync_state='PUSHED',
                        last_image_task_token=token,
                        last_pushed_at=timezone.now()
                    )
                else:
                    logger.warning(f"MQTT Publish failed for tag {tag.tag_mac}")
                    handle_tag_failure_task.delay(tag.id)

        except Exception as e:
            logger.exception(f"Error processing tag {tag.tag_mac} in gateway queue: {str(e)}")
            handle_tag_failure_task.delay(tag.id)

        tags_processed_count += 1

        # 5. Precise Delay
        # We wait the specified delay between EACH tag.
        time.sleep(delay_seconds)

    # 6. Chain if there's potentially more work (reached time limit)
    logger.info(f"Reached time budget for gateway {gateway_id} queue. Re-triggering.")
    process_gateway_queue_task.delay(gateway_id)
    return f"Time budget reached. Processed: {tags_processed_count}"

@shared_task(name="core.tasks.handle_tag_failure_task")
def handle_tag_failure_task(tag_id):
    """
    RETRY LOGIC
    -----------
    Implements 5m, 15m, 30m backoff for failed updates.
    """
    try:
        from .models import ESLTag
        tag = ESLTag.objects.get(pk=tag_id)

        # SAFETY: If the tag has already succeeded (e.g. late result received), don't retry.
        if tag.sync_state == 'SUCCESS':
            return "Skipping retry: Tag already successful"

        # Lock to prevent concurrent retry triggers
        lock_key = f"retry_lock_{tag_id}"
        if not cache.add(lock_key, "locked", 10):
            return "Retry already in progress"

        if tag.retry_count < 3:
            tag.retry_count += 1
            ESLTag.objects.filter(pk=tag.pk).update(retry_count=tag.retry_count, sync_state='RETRY_WAITING')

            # Backoff: 5m, 15m, 30m
            delays = [300, 900, 1800]
            delay = delays[tag.retry_count - 1]

            logger.info(f"Scheduling retry #{tag.retry_count} for {tag.tag_mac} in {delay}s")
            update_tag_image_task.apply_async(kwargs={'tag_id': tag_id, 'is_retry': True}, countdown=delay)
            return f"Retry #{tag.retry_count} scheduled"
        else:
            tag.sync_state = 'PUSH_FAILED'
            tag.save()
            logger.warning(f"Max retries reached for tag {tag.tag_mac}")
            return "Max retries reached"
    except Exception:
        logger.exception(f"Error in handle_tag_failure_task for {tag_id}")
        return "Failure handling failed"

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
    Runs every minute. Marks gateways as offline if they miss 4 heartbeats.
    Uses a lightweight bulk-query approach to avoid 'hammering' the system.
    """
    try:
        from django.db.models import F, ExpressionWrapper, DurationField, Q

        # Load multiplier from Global Settings (Default: 4x)
        multiplier = int(GlobalSetting.objects.filter(key='OFFLINE_TIMEOUT_MULTIPLIER').values_list('value', flat=True).first() or 4)
        now = timezone.now()

        # LIGHTWEIGHT BATCH PROCESSING:
        # We group gateways by their heartbeat_interval to run minimal SQL updates.
        intervals = Gateway.objects.exclude(is_online='OFFLINE').values_list('heartbeat_interval', flat=True).distinct()

        count_offline = 0
        for interval_val in intervals:
            # Safe default for hardware is 15 seconds if unknown
            interval = interval_val or 15
            timeout_seconds = interval * multiplier
            cutoff = now - timezone.timedelta(seconds=timeout_seconds)

            # Update all online/error gateways with THIS interval that haven't been seen since the cutoff.
            # update() runs a single SQL query: UPDATE ... WHERE ...
            updated = Gateway.objects.exclude(
                is_online='OFFLINE'
            ).filter(
                heartbeat_interval=interval_val,
                last_heartbeat__lt=cutoff
            ).update(
                is_online='OFFLINE',
                last_error_message=f"Offline: No heartbeat received for {timeout_seconds}s (Checked at {now.strftime('%H:%M:%S')})"
            )
            count_offline += updated

        # Handle edge case: Gateways that never sent a heartbeat (last_heartbeat is null)
        # but have been created longer than 4x 15s ago.
        orphaned_cutoff = now - timezone.timedelta(seconds=15 * multiplier)
        updated_orphans = Gateway.objects.exclude(
            is_online='OFFLINE'
        ).filter(
            last_heartbeat__isnull=True,
            created_at__lt=orphaned_cutoff
        ).update(
            is_online='OFFLINE',
            last_error_message="Offline: Never received initial heartbeat"
        )
        count_offline += updated_orphans

        if count_offline > 0:
            logger.info(f"Gateway Status Check: Marked {count_offline} gateways as OFFLINE.")

        # NEW: Check for Tag Sync Timeouts (Requested: 60 seconds)
        # If a tag has been in 'PUSHED' state for more than 60 seconds, trigger retry logic
        timeout_cutoff = now - timezone.timedelta(seconds=60)
        timed_out_tags = list(ESLTag.objects.filter(
            sync_state='PUSHED',
            last_pushed_at__lt=timeout_cutoff
        ).values_list('id', flat=True))

        count_tag_timeouts = 0
        for tid in timed_out_tags:
            handle_tag_failure_task.delay(tid)
            count_tag_timeouts += 1

        if count_tag_timeouts > 0:
            logger.info(f"Triggered retry for {count_tag_timeouts} tags due to 60s timeout.")

        # NEW: Restart stalled queues (in case a worker died)
        # We look for any tag in 'IMAGE_READY' state and ensure its gateway's queue is active.
        gateways_with_pending = list(Gateway.objects.filter(tags__sync_state='IMAGE_READY').values_list('estation_id', flat=True).distinct())
        for gw_id in gateways_with_pending:
            trigger_gateway_processing(gw_id)

        return f"Checked status. Marked {count_offline} gateways offline and {count_tag_timeouts} tag timeouts."
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
