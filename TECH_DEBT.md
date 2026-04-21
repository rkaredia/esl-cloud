# Tech Debt & Improvement Backlog

This document tracks identified technical debt, architectural bottlenecks, and proposed improvements for the SAIS Platform.

## Scoring Methodology
Items are ranked by **Priority Score** = (Value / Effort), where:
- **Value (1-5)**: Impact on performance, security, or maintainability.
- **Effort (1-5)**: Estimated developer time (1=Low, 5=High).
- **High Rank** = Quick Wins (High Value, Low Effort).

---

## 1. MAC Address Normalization & Indexed Lookup
*   **Rank**: 5.0 (Value: 5, Effort: 1)
*   **Issue**: Current hardware lookups in `mqtt_client.py` use database-level string replacements (`Replace('tag_mac', Value(':'), Value(''))`) to match incoming packets. This bypasses indexes, leading to $O(N)$ table scans.
*   **Proposed Fix**:
    - Centralize MAC normalization in `core/utils.py`.
    - Enforce a strict colon-free format in `ESLTag.save()`.
    - Add a database index on `tag_mac`.
*   **Benefit**: Instant $O(1)$ tag identification during bulk hardware updates.

## 2. Hardcoded Default Hardware IPs
*   **Rank**: 4.0 (Value: 4, Effort: 1)
*   **Issue**: `configure_gateway_view` contains hardcoded default server IPs (e.g., `192.168.1.92`).
*   **Proposed Fix**: Move these to `GlobalSetting` or `settings.py`.
*   **Benefit**: Faster environment setup and reduced risk of misconfiguration in production.

## 3. MQTT Database Logging Volume
*   **Rank**: 2.5 (Value: 5, Effort: 2)
*   **Issue**: Every MQTT packet (including high-frequency heartbeats) is stored in the `MQTTMessage` table. In large stores, this table will grow by millions of rows monthly, slowing down the Admin UI.
*   - **Proposed Fix**:
    - Implement a "Sampling" mode for heartbeats.
    - Move raw logs to a dedicated time-series store or structured files.
    - Keep only critical "Result" and "Error" messages in the main DB.
*   **Benefit**: Sustained database performance and reduced hosting costs.

## 4. Redundant Template Geometry Logic
*   **Rank**: 1.5 (Value: 3, Effort: 2)
*   **Issue**: `utils.py` and `base.py` (Mock Renderer) have overlapping logic for calculating text bounds and safe pads.
*   **Proposed Fix**: Extract geometry calculation helpers into a standalone `LayoutEngine` class.
*   **Benefit**: Easier to implement "Pixel Perfect" visual changes across all templates simultaneously.

## 5. Background Task Observability
*   **Rank**: 1.5 (Value: 3, Effort: 2)
*   **Issue**: Failures in `update_tag_image_task` are logged but hard to track in bulk.
*   **Proposed Fix**: Integrate `django-celery-results` more deeply or add a "Sync Health" summary view.
*   **Benefit**: Faster troubleshooting of hardware delivery issues.
