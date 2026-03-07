## 2026-03-06 - [Admin Filter Toggle & Global A11y]
**Learning:** Generic "Filters" buttons without state feedback or ARIA attributes are inaccessible to screen readers and confusing for users. Store selectors using non-semantic spans fail to provide clear input context.
**Action:** Always implement dynamic button text (Show/Hide), `aria-expanded`, and `aria-controls` for toggleable UI elements. Use semantic `<label for="...">` associations for all form controls, even in the admin header.
