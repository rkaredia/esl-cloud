## 2025-05-15 - [Font Loading and Scaling Optimization]
**Learning:** Pillow's `ImageFont.truetype` is a heavy I/O and CPU operation. Loading the same font multiple times during image generation (especially in a resizing loop) causes significant latency. Additionally, linear search for fitting text to a bounding box is $O(N)$ and can be optimized to $O(\log N)$ using binary search.
**Action:** Always cache `ImageFont` objects and use binary search for dynamic font scaling to ensure high-performance image generation.
