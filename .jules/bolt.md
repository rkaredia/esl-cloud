## 2025-05-15 - [Font Loading and Scaling Optimization]
**Learning:** Pillow's `ImageFont.truetype` is a heavy I/O and CPU operation. Loading the same font multiple times during image generation (especially in a resizing loop) causes significant latency. Additionally, linear search for fitting text to a bounding box is $O(N)$ and can be optimized to $O(\log N)$ using binary search.
**Action:** Always cache `ImageFont` objects and use binary search for dynamic font scaling to ensure high-performance image generation.

## 2026-03-06 - [Django Task Query Optimization]
**Learning:** Background tasks involving models with FileFields often trigger "hidden" lazy-loading queries during storage path generation (via `upload_to` functions) and model validation. Using `select_related` for these relationships and preferring `QuerySet.update()` over `instance.save()` for state transitions can drastically reduce database round-trips.
**Action:** Always prefetch nested relationships used in `upload_to` paths or validation logic within Celery tasks, and use direct SQL updates for simple status changes to bypass model lifecycle overhead.

## 2026-03-07 - [Redundant Query and Object Instantiation in Bulk Tasks]
**Learning:** Iterating over a large QuerySet to queue tasks (e.g., `.delay()`) triggers full model instantiation and a massive database payload. Additionally, calling `.count()` on the same QuerySet later results in a redundant `SELECT COUNT(*)` query.
**Action:** Use `.values_list('id', flat=True)` for task queueing loops to minimize memory and DB overhead. Wrap the QuerySet in `list()` to evaluate once, allowing the use of `len()` for logging/counting without a second DB trip.

## 2026-03-08 - [Redundant Task Triggering and Initialization Recursion]
**Learning:** Having task triggering logic in both model `.save()` and `post_save` signals causes $2N$ tasks to be queued for every save. Additionally, accessing fields in `__init__` snapshots can trigger lazy-loading recursion if not careful.
**Action:** Centralize background task triggering in Django signals to ensure a single source of truth and prevent redundant processing. Use `self.__dict__.get('field')` in `__init__` to safely snapshot original data without triggering unintended database queries or recursion.

## 2026-03-09 - [MQTT Heartbeat Processing Optimization]
**Learning:** Processing large batches of MQTT heartbeats (e.g., 500+ tags) using individual `.save()` calls creates an O(N) database bottleneck. `bulk_update` and `bulk_create` reduce this to O(1). However, `bulk_update` does not trigger `auto_now` fields, so `updated_at` must be manually set. Deduplication of incoming data is also necessary to prevent `IntegrityError` during `bulk_create`.
**Action:** Use dict-based deduplication and Django bulk operations for high-frequency hardware signal processing. Manually update timestamp fields when using bulk methods on models with `AuditModel` or `auto_now` fields.
