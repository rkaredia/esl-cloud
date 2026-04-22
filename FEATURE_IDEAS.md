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

## 2. Interactive Store Health Heatmap
**Problem:** `ESLTag` has `aisle`, `section`, and `shelf_row` metadata, but it's only used for filtering in list views.
**Feature:** A "Store Map" view in the Admin Dashboard that renders a visual grid representation of the store's physical layout.
**Implementation:**
- Custom Django Admin view using CSS Grid.
- Aggregates tag health (Battery, Sync State) by `aisle` and `section`.
- Color-coded indicators for quick identification of issues.
**Value:** Speeds up physical maintenance and troubleshooting.

## 3. Dynamic QR Code Integration
**Problem:** Physical tags often have unused white space. Customers want more info than fits on a small E-ink screen.
**Feature:** Dynamic QR codes on tag templates linking to product reviews, dietary info, or mobile checkout.
**Implementation:**
- Add `qr_url` field to `Product`.
- Update `LayoutEngine` in `core/utils.py` to generate QR codes (e.g., using `python-qrcode`).
- Enhance templates (e.g., V3) to include the QR code.
**Value:** Connects physical retail to digital content and services.
