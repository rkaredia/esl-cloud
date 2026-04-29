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
from .utils import generate_esl_image, trigger_bulk_sync
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
    # 0. SYSTEM RETRY CHECK:
    # If this is an automatic system retry (backoff), we only proceed if
    # the tag hasn't already succeeded or been manually refreshed in between.
    if is_retry:
        tag_status = ESLTag.objects.filter(pk=tag_id).values_list('sync_state', flat=True).first()
        if tag_status != 'RETRY_WAITING':
            logger.info(f"Tag {tag_id} no longer in RETRY_WAITING (is: {tag_status}). Aborting retry task.")
            return "Retry aborted: Status changed"

    try:
        # Small random delay to prevent 'Thundering Herd'
        time.sleep(random.uniform(0, 0.1))

        # DEDUPLICATION & EFFICIENCY:
        # If a tag is already being processed (IMAGE GENERATION), skip redundant refreshes.
        # We allow overriding PUSHED (sent to gateway) or RETRY_WAITING (backoff) states
        # if a user manually triggers a refresh.
        if not is_retry:
            tag_status = ESLTag.objects.filter(pk=tag_id).values_list('sync_state', flat=True).first()
            if tag_status in ['PROCESSING', 'IMAGE_READY']:
                logger.info(f"Tag {tag_id} is currently being processed ({tag_status}). Skipping redundant refresh.")
                return "Skipped: Already processing"

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
        
        # RELEASE LOCK: Image is generated, we can now allow new generation tasks if needed.
        cache.delete(lock_id)

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

        # REAL-TIME CONNECTIVITY CHECK
        # We fetch ALL gateways for this store and verify their heartbeat status
        all_store_gateways = list(Gateway.objects.filter(store=tag.store))
        online_gateways = [gw for gw in all_store_gateways if gw.is_currently_online()]

        if not online_gateways:
            # If NO gateways are online for the store, we mark it as a terminal failure
            # or trigger a retry which will eventually land in PUSH_FAILED.
            logger.warning(f"No online gateways found for store {tag.store.name}. Tag {tag.tag_mac} push aborted.")
            cache.delete(lock_id)

            # Update sync_state to PUSH_FAILED immediately for accurate reporting
            ESLTag.objects.filter(pk=tag_id).update(sync_state='PUSH_FAILED')
            return "Gateway push failed: All gateways offline"

        # FAILOVER ROTATION STRATEGY
        # We prioritize the last successful gateway, then the assigned one,
        # but ONLY if they are currently online.
        online_ids = {gw.estation_id for gw in online_gateways}
        gateways_to_try = []

        if tag.last_successful_gateway_id in online_ids:
            gateways_to_try.append(tag.last_successful_gateway_id)

        if tag.gateway and tag.gateway.estation_id in online_ids and tag.gateway.estation_id not in gateways_to_try:
            gateways_to_try.append(tag.gateway.estation_id)

        # Add remaining online gateways to the rotation
        for gw in online_gateways:
            if gw.estation_id not in gateways_to_try:
                gateways_to_try.append(gw.estation_id)

        if not gateways_to_try:
            cache.delete(lock_id)
            ESLTag.objects.filter(pk=tag_id).update(sync_state='PUSH_FAILED')
            return "Gateway push failed: No available online gateways"

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

    # 1. Ensure the lock is held to prevent other workers from starting a parallel loop.
    # TTL of 60 seconds is ample.
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
                # IMPORTANT: Only delete if it's been less than 45s, otherwise we might delete a NEW task's lock
                if (time.time() - start_time) < 45:
                    cache.delete(lock_key)
                return f"Queue empty. Processed: {tags_processed_count}"

            # Mark as 'PROCESSING' immediately to claim it
            ESLTag.objects.filter(pk=tag.pk).update(sync_state='PROCESSING')
            # Update in-memory object to keep it consistent without an extra query
            tag.sync_state = 'PROCESSING'

        # 4. Prepare and Send
        try:
            # Redundant refresh_from_db() removed for performance ($O(1)$ query reduction per tag)
            tag_mac = tag.tag_mac.upper()

            # REAL-TIME CONNECTIVITY VERIFICATION
            # If the gateway went offline since the task was queued, trigger a failure/retry
            if not tag.gateway.is_currently_online():
                 logger.warning(f"Gateway {gateway_id} is OFFLINE. Aborting push for tag {tag_mac}.")
                 handle_tag_failure_task.delay(tag.id, reason="Gateway Offline during delivery")
                 continue

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
                    handle_tag_failure_task.delay(tag.id, reason="MQTT Publish Failed")

        except Exception as e:
            logger.exception(f"Error processing tag {tag.tag_mac} in gateway queue: {str(e)}")
            handle_tag_failure_task.delay(tag.id, reason=f"Queue Error: {str(e)}")

        tags_processed_count += 1

        # 5. Precise Delay
        # We wait the specified delay between EACH tag.
        time.sleep(delay_seconds)

    # 6. Chain if there's potentially more work (reached time limit)
    logger.info(f"Reached time budget for gateway {gateway_id} queue. Re-triggering.")
    process_gateway_queue_task.delay(gateway_id)
    return f"Time budget reached. Processed: {tags_processed_count}"

@shared_task(name="core.tasks.handle_tag_failure_task")
def handle_tag_failure_task(tag_id, reason="Unknown"):
    """
    RETRY LOGIC
    -----------
    Implements 5m, 15m, 30m backoff for failed updates.
    """
    try:
        from .models import ESLTag, MQTTMessage
        tag = ESLTag.objects.get(pk=tag_id)

        # 1. SUCCESS CHECK: Don't retry if the tag already succeeded
        if tag.sync_state == 'SUCCESS':
            return "Skipping retry: Tag already successful"

        # 2. LOGGING & AUDIT: Record the failure in the system logs for visibility
        if reason == "Timeout" and tag.gateway:
            # Create a synthetic MQTT log entry to ensure the 'stuck' tag is visible in audit logs
            MQTTMessage.objects.create(
                direction='received',
                estation_id=tag.gateway.estation_id,
                topic=f"/estation/{tag.gateway.estation_id}/timeout",
                data=f"TIMEOUT FAILURE: No response from tag {tag.tag_mac} after 60 seconds.",
                is_success=False
            )

        # Lock to prevent concurrent retry triggers
        lock_key = f"retry_lock_{tag_id}"
        if not cache.add(lock_key, "locked", 15):
            # If the lock is held, it means another worker is already processing the failure
            # for this specific tag. We return so we don't duplicate the retry schedule.
            logger.info(f"Retry/Failure processing already in progress for {tag.tag_mac}. Skipping.")
            return "Retry already in progress"

        if tag.retry_count < 3:
            tag.retry_count += 1
            ESLTag.objects.filter(pk=tag.pk).update(retry_count=tag.retry_count, sync_state='RETRY_WAITING')

            # Backoff: 5m, 15m, 30m
            delays = [300, 900, 1800]
            delay = delays[tag.retry_count - 1]

            logger.info(f"Scheduling retry #{tag.retry_count} for {tag.tag_mac} due to {reason}. (Attempt: {tag.retry_count}, Delay: {delay}s)")
            update_tag_image_task.apply_async(kwargs={'tag_id': tag_id, 'is_retry': True}, countdown=delay)
            return f"Retry #{tag.retry_count} scheduled"
        else:
            tag.sync_state = 'PUSH_FAILED'
            tag.save()
            logger.warning(f"Max retries reached for tag {tag.tag_mac} (Final failure: {reason})")
            return f"Max retries reached: {reason}"
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

        if tag_ids:
            # Performance: Use trigger_bulk_sync (O(1) Redis round-trips via Celery group)
            # instead of a manual O(N) loop of .delay() calls.
            trigger_bulk_sync(tag_ids)

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
        # We also catch tags stuck in 'PROCESSING' for more than 5 minutes.
        timeout_cutoff = now - timezone.timedelta(seconds=60)
        stuck_cutoff = now - timezone.timedelta(minutes=5)

        timed_out_tags = list(ESLTag.objects.filter(
            Q(sync_state='PUSHED', last_pushed_at__lt=timeout_cutoff) |
            Q(sync_state='PROCESSING', updated_at__lt=stuck_cutoff)
        ).values_list('id', flat=True))

        count_tag_timeouts = 0
        for tid in timed_out_tags:
            # We don't countdown here, we call immediately so it enters RETRY_WAITING or PUSH_FAILED
            handle_tag_failure_task.delay(tid, reason="Timeout")
            count_tag_timeouts += 1

        if count_tag_timeouts > 0:
            logger.info(f"Triggered recovery/retry for {count_tag_timeouts} tags due to timeout or stuck processing.")

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
