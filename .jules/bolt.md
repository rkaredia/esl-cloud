## 2025-05-15 - [Font Loading and Scaling Optimization]
**Learning:** Pillow's `ImageFont.truetype` is a heavy I/O and CPU operation. Loading the same font multiple times during image generation (especially in a resizing loop) causes significant latency. Additionally, linear search for fitting text to a bounding box is $O(N)$ and can be optimized to $O(\log N)$ using binary search.
**Action:** Always cache `ImageFont` objects and use binary search for dynamic font scaling to ensure high-performance image generation.

## 2026-03-06 - [Django Task Query Optimization]
**Learning:** Background tasks involving models with FileFields often trigger "hidden" lazy-loading queries during storage path generation (via `upload_to` functions) and model validation. Using `select_related` for these relationships and preferring `QuerySet.update()` over `instance.save()` for state transitions can drastically reduce database round-trips.
**Action:** Always prefetch nested relationships used in `upload_to` paths or validation logic within Celery tasks, and use direct SQL updates for simple status changes to bypass model lifecycle overhead.
