## 2026-03-06 - [Admin Filter Toggle & Global A11y]
**Learning:** Generic "Filters" buttons without state feedback or ARIA attributes are inaccessible to screen readers and confusing for users. Store selectors using non-semantic spans fail to provide clear input context.
**Action:** Always implement dynamic button text (Show/Hide), `aria-expanded`, and `aria-controls` for toggleable UI elements. Use semantic `<label for="...">` associations for all form controls, even in the admin header.

## 2026-03-07 - [Header Navigation & A11y Polish]
**Learning:** Hardcoded instructions in `title` attributes (e.g., for bulk operations) are often missed by screen reader users. Global navigation links like "Dashboard" should be consistently available in the header for power users.
**Action:** Always supplement `title` instructions with `aria-label` for key action buttons. Ensure primary overview pages (like Dashboards) have high-visibility links in the global admin header.
