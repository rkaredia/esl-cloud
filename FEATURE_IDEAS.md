# Proposed Feature Ideas

This document outlines three high-value features for the SAIS Platform based on the existing architecture.

## 1. Automated Promotional Scheduling
**Problem:** Currently, the `is_on_special` flag on the `Product` model is a static boolean. Store managers must manually toggle this to change the visual template of a tag.
**Feature:** Implement a `Promotion` model that allows users to schedule "Special" pricing with a `start_datetime` and `end_datetime`.
**Implementation:**
- Use `django-celery-beat` for periodic tasks.
- Task checks for active/inactive promotions and updates `Product` fields.
- Triggers existing `update_tags_on_product_change` signal.
**Value:** Automates manual labor and ensures pricing synchronization.

## 2. Interactive Store Health Heatmap (Expanded)
**Problem:** ESL Tags are physically distributed across large retail environments. While the `ESLTag` model stores `aisle`, `section`, and `shelf_row`, the standard list-view makes it difficult to visualize the physical distribution of hardware issues.

**Feature:** A bird's-eye "Heatmap" dashboard that represents the store as a grid of interactive blocks.

**Functional Details:**
- **Hierarchical Aggregation:** Tags are grouped by `aisle` (Card) and then by `section` (Block).
- **Health State Logic:**
    - **🔴 Red (Critical):** Any tag in the section has a terminal failure (`PUSH_FAILED`, `GEN_FAILED`).
    - **🟠 Amber (Warning):** No failures, but one or more tags have low battery (`<= 10%`).
    - **🟢 Green (Healthy):** All tags are in `SUCCESS` or `PUSHED` states with healthy battery levels.
    - **⚪ Gray (Empty):** No tags are assigned to this location.
- **Interactive Drill-down:** Clicking a "Section Block" uses Django's admin URL patterns to redirect the user to the ESL Tag list view, automatically pre-filtered by `aisle` and `section` (e.g., `?aisle=1&section=A`).

**Technical Implementation:**
- **Backend:** A custom Django Admin view (`heatmap_view`) that uses `.values('aisle', 'section').annotate()` to perform a single efficient DB aggregation per store load.
- **Frontend:** A responsive CSS Grid layout (see `STORE_HEATMAP_MOCKUP.html` for the visual prototype).
- **Tooltips:** Hovering over a block displays a summary (e.g., "Aisle 4, Section 2: 15 Tags, 2 Low Battery").

**Value:** Reduces "Mean Time To Repair" (MTTR) by allowing technicians to go directly to the physical shelf location of a failed device.

## 3. Dynamic QR Code Integration
**Problem:** Physical tags often have unused white space. Customers want more info than fits on a small E-ink screen.
**Feature:** Dynamic QR codes on tag templates linking to product reviews, dietary info, or mobile checkout.
**Implementation:**
- Add `qr_url` field to `Product`.
- Update `LayoutEngine` in `core/utils.py` to generate QR codes (e.g., using `python-qrcode`).
- Enhance templates (e.g., V3) to include the QR code.
**Value:** Connects physical retail to digital content and services.

## 4. Visual Picking & Stock-Find Assistance (LED Signaling)
**Problem:** Store staff often lose time physically locating specific products during "Click & Collect" picking or inventory restocking, especially in large aisles.
**Feature:** Implement a "Locate Product" action in the admin. When triggered, the system sends an MQTT command to the tag to flash its onboard LED for a defined duration (e.g., 30 seconds).
**Implementation:**
- Modify `ESLMqttClient.publish_tag_update` to accept an `led_pattern` parameter (utilizing the `Pattern` and `Times` fields in the `taskESL` protocol).
- Add a custom Admin Action to the `ESLTagAdmin` called "Flash LED to Locate".
**Value:** Significantly reduces "Mean Time to Locate" for staff, improving operational efficiency.

## 5. Predictive Battery Lifecycle Analytics
**Problem:** While the system tracks current battery percentage, it doesn't account for the *rate* of discharge. Tags in high-traffic aisles (updated more frequently) will fail sooner than others, leading to unexpected "dead zones" on the shelf.
**Feature:** A "Maintenance Forecast" dashboard that predicts "Estimated End of Life" for tag batteries.
**Implementation:**
- Create a `BatteryHistory` model to periodically snapshot tag levels.
- Use a background task to calculate the average discharge rate per tag based on update frequency.
- Generate a "Replacement Batch" report in the Analytics Dashboard for upcoming battery swaps.
**Value:** Shifts maintenance from reactive to proactive, ensuring 100% shelf-edge visibility.

## 6. Integrated Stock-Level & "Restocking" Templates
**Problem:** E-ink tags currently only show customer-facing info (Price/Name). Floor staff have no immediate visual indicator of inventory levels without checking a separate handheld device.
**Feature:** Dynamic "Stock-Aware" templates that automatically display inventory badges or "Restocking" alerts when levels fall below a threshold.
**Implementation:**
- Add `stock_quantity` and `low_stock_threshold` to the `Product` model.
- Update `LayoutEngine` (e.g., Template V3) to render a "Low Stock" icon or "Back-ordered" text if the threshold is met.
- Trigger tag refreshes via existing signals on inventory updates.
**Value:** Enhances customer transparency and prioritizes restocking tasks for staff directly at the shelf edge.
