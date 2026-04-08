## 2026-03-06 - [Admin Filter Toggle & Global A11y]
**Learning:** Generic "Filters" buttons without state feedback or ARIA attributes are inaccessible to screen readers and confusing for users. Store selectors using non-semantic spans fail to provide clear input context.
**Action:** Always implement dynamic button text (Show/Hide), `aria-expanded`, and `aria-controls` for toggleable UI elements. Use semantic `<label for="...">` associations for all form controls, even in the admin header.

## 2026-03-07 - [Header Navigation & A11y Polish]
**Learning:** Hardcoded instructions in `title` attributes (e.g., for bulk operations) are often missed by screen reader users. Global navigation links like "Dashboard" should be consistently available in the header for power users.
**Action:** Always supplement `title` instructions with `aria-label` for key action buttons. Ensure primary overview pages (like Dashboards) have high-visibility links in the global admin header.

## 2026-03-08 - [Grid Fluidity & Emoji-based Sidebar Icons]
**Learning:** Overly rigid 'minmax' values in CSS grids (e.g., 240px+) can cause layout breaking on smaller viewports before media queries kick in. When direct CSS pseudo-element injection is insufficient for dynamic sidebar categories, using emojis directly in the category 'name' string provides a reliable, cross-browser visual affordance.
**Action:** Use fluid grid constraints (e.g., minmax(200px, 1fr)) and supplement with media queries for tighter mobile layouts. Favor emoji-based category icons in 'get_app_list' for immediate visual feedback in the Django Admin sidebar.

## 2026-03-09 - [Decorative Symbol Isolation & Admin Security]
**Learning:** Decorative status symbols (dots, arrows) in list views and buttons are announced as literal characters (e.g., "bullet") by screen readers, cluttering the experience.
**Action:** Always wrap decorative symbols in `<span aria-hidden="true">` when adjacent to meaningful status text. Favor `format_html` over `mark_safe` in Django Admin to ensure both security (XSS prevention) and accessibility.

## 2026-03-10 - [Feedback Loops & Async Interaction]
**Learning:** Users perceive long-running or redirect-heavy operations (like hardware syncs or gallery refreshes) as "broken" if there is no immediate visual state change. Pure CSS animations combined with simple JS class toggling provide the best balance of performance and responsiveness.
**Action:** Implement `.btn-loading` and `.spinning` utility classes for all action-oriented buttons. Ensure `onload` and `onerror` event listeners are used to reset state for multi-asset loading operations like image galleries.
