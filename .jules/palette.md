## 2025-05-14 - [Search Shortcut UX]
**Learning:** Selecting search input text on focus via keyboard shortcut allows for much faster "search-refine-search" loops compared to just focusing the cursor.
**Action:** Always call `select()` after `focus()` on search inputs triggered by global shortcuts.
