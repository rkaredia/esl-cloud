/*
   SAIS ADMIN INTERACTIVITY ENGINE
   -------------------------------
   This script enhances the standard Django Admin with modern features
   like column resizing, live-updating logs, and keyboard shortcuts.
*/

document.addEventListener('DOMContentLoaded', function() {

    // 1. COLUMN RESIZING LOGIC
    // Allows users to drag table headers to change column width.
    const table = document.getElementById('result_list');
    if (table) {
        const headerRow = table.querySelector('thead tr');
        if (headerRow) {
            const cols = headerRow.querySelectorAll('th');

            // Get model name from body class to save widths per model
            const modelMatch = document.body.className.match(/model-(\w+)/);
            const modelName = modelMatch ? modelMatch[1] : 'unknown';

            // Load saved widths from the browser's LocalStorage
            const storedWidths = JSON.parse(localStorage.getItem(`sais-admin-cols-${modelName}`) || '{}');

            cols.forEach((col, index) => {
                // Apply previously saved width
                if (storedWidths[index]) {
                    col.style.width = storedWidths[index] + 'px';
                }

                // Inject a invisible 'resizer' handle into the column header
                const resizer = document.createElement('div');
                resizer.classList.add('resizer');
                resizer.title = 'Drag to resize column';
                col.appendChild(resizer);

                let x = 0;
                let w = 0;

                // Capture starting mouse position
                const mouseDownHandler = function(e) {
                    x = e.clientX;
                    w = parseInt(window.getComputedStyle(col).width, 10);
                    document.addEventListener('mousemove', mouseMoveHandler);
                    document.addEventListener('mouseup', mouseUpHandler);
                    resizer.classList.add('resizing');
                };

                // Resize column as mouse moves
                const mouseMoveHandler = function(e) {
                    const dx = e.clientX - x;
                    col.style.width = `${w + dx}px`;
                };

                // Save final width to LocalStorage when mouse is released
                const mouseUpHandler = function() {
                    document.removeEventListener('mousemove', mouseMoveHandler);
                    document.removeEventListener('mouseup', mouseUpHandler);
                    resizer.classList.remove('resizing');

                    const newWidths = JSON.parse(localStorage.getItem(`sais-admin-cols-${modelName}`) || '{}');
                    newWidths[index] = parseInt(col.style.width, 10);
                    localStorage.setItem(`sais-admin-cols-${modelName}`, JSON.stringify(newWidths));
                };

                resizer.addEventListener('mousedown', mouseDownHandler);
            });
        }
    }

    // 2. FILTER TOGGLING LOGIC
    // Hides the bulky right-hand filter sidebar by default.
    const changelistWrapper = document.getElementById('changelist-wrapper');
    const filter = document.getElementById('changelist-filter');
    if (changelistWrapper && filter) {

        const updateToggleButton = (btn, isVisible) => {
            if (!btn) return;
            const text = isVisible ? 'Hide Filters' : 'Show Filters';
            const icon = ' <span aria-hidden="true">👁</span> [F]';
            btn.innerHTML = text + icon;
            btn.setAttribute('aria-expanded', isVisible);

            // Sync all toggle buttons if multiple exist
            document.querySelectorAll('#filter-toggle-btn').forEach(b => {
                b.innerHTML = text + icon;
            });
        };

        // Load preference: Standard behavior is 'hidden' unless explicitly set to 'true'
        const filterState = localStorage.getItem('sais-admin-filter-visible');
        const isInitiallyVisible = filterState === 'true';

        if (isInitiallyVisible) {
            changelistWrapper.classList.remove('filter-hidden');
        }

        let toggleBtn = document.getElementById('filter-toggle-btn') || document.getElementById('toggle-filters');

        // If no toggle button exists (on some standard models), create one automatically
        if (!toggleBtn) {
            const objectTools = document.querySelector('.object-tools');
            if (objectTools) {
                const toggleItem = document.createElement('li');
                toggleBtn = document.createElement('a');
                toggleBtn.id = 'filter-toggle-btn';
                toggleBtn.href = 'javascript:void(0);';
                toggleBtn.className = 'addlink filter-toggle';
                toggleBtn.setAttribute('aria-controls', 'changelist-filter');
                toggleItem.appendChild(toggleBtn);
                objectTools.insertBefore(toggleItem, objectTools.firstChild);
            }
        }

        if (toggleBtn) {
            updateToggleButton(toggleBtn, isInitiallyVisible);

            // SECURITY: Use event delegation to ensure the click listener is robust
            document.addEventListener('click', function(e) {
                if (e.target && (e.target.id === 'filter-toggle-btn' || e.target.id === 'toggle-filters' || e.target.closest('#filter-toggle-btn'))) {
                    const btn = e.target.id === 'filter-toggle-btn' ? e.target : (e.target.id === 'toggle-filters' ? e.target : e.target.closest('#filter-toggle-btn'));
                    e.preventDefault();
                    changelistWrapper.classList.toggle('filter-hidden');
                    const isVisible = !changelistWrapper.classList.contains('filter-hidden');
                    localStorage.setItem('sais-admin-filter-visible', isVisible);
                    updateToggleButton(btn, isVisible);
                }
            });
        }
    }

    // 3. STORE SELECTION LOGIC
    // SECURITY: We use an external event listener rather than an inline 'onchange'
    // attribute to follow modern security practices (CSP compliance).
    const storeSelect = document.getElementById('header-store-select');
    if (storeSelect) {
        storeSelect.addEventListener('change', function() {
            if (this.value) {
                // Redirect to the setter view with the chosen store ID
                window.location.href = '/set-store/' + encodeURIComponent(this.value) + '/';
            }
        });
    }

    // 4. MQTT LIVE REFRESH LOGIC
    // Specifically for the technical MQTT communication logs.
    const isMQTTLogPage = document.body.classList.contains('model-mqttmessage') ||
                          window.location.pathname.includes('/core/mqttmessage/');

    if (isMQTTLogPage && document.body.classList.contains('change-list')) {
        let objectTools = document.querySelector('.object-tools');
        if (!objectTools) objectTools = document.querySelector('#content-main ul.object-tools');

        if (objectTools) {
            const refreshItem = document.createElement('li');
            const refreshBtn = document.createElement('a');
            refreshBtn.id = 'mqtt-live-toggle';
            refreshBtn.href = 'javascript:void(0);';
            refreshBtn.className = 'addlink';
            refreshBtn.style.background = '#059669';

            let isLive = localStorage.getItem('mqtt-live-refresh') === 'true';
            let refreshInterval = null;

            const updateBtn = () => {
                refreshBtn.innerHTML = isLive ? 'Live: ON 🟢' : 'Live: OFF ⚪';
                refreshBtn.style.background = isLive ? '#059669' : '#64748b';
            };

            const startRefresh = () => {
                refreshInterval = setInterval(() => {
                    // Auto-reload the page every 5 seconds to show new hardware messages
                    window.location.reload();
                }, 5000);
            };

            refreshBtn.addEventListener('click', () => {
                isLive = !isLive;
                localStorage.setItem('mqtt-live-refresh', isLive);
                updateBtn();
                if (isLive) startRefresh();
                else if (refreshInterval) clearInterval(refreshInterval);
            });

            updateBtn();
            if (isLive) startRefresh();

            refreshItem.appendChild(refreshBtn);
            objectTools.appendChild(refreshItem);
        }

        // COPY-TO-CLIPBOARD: Clicking a payload snippet copies it to the clipboard.
        document.querySelectorAll('.field-data_preview code').forEach(code => {
            code.style.cursor = 'pointer';
            code.title = 'Click to copy full payload';
            code.addEventListener('click', function() {
                const fullData = this.innerText;
                navigator.clipboard.writeText(fullData).then(() => {
                    const originalText = this.innerText;
                    this.innerText = 'Copied! ✅';
                    setTimeout(() => { this.innerText = originalText; }, 1000);
                });
            });
        });
    }

    // 5. GLOBAL KEYBOARD SHORTCUTS
    // Enhance productivity for power users.
    const searchInput = document.querySelector('input[name="q"]');
    if (searchInput && !searchInput.placeholder.includes('[/]')) {
        searchInput.placeholder += ' [/]';
    }

    // 6. MAC ADDRESS CLICK-TO-COPY (Enhanced with Keyboard A11y)
    const copyMacToClipboard = (macField) => {
        const macAddress = macField.innerText.trim();
        if (macAddress && macAddress !== '-') {
            navigator.clipboard.writeText(macAddress).then(() => {
                const originalContent = macField.innerHTML;
                macField.style.width = macField.offsetWidth + 'px'; // Prevent layout shift
                macField.innerHTML = '<span style="color: #059669; font-weight: bold;">Copied! ✅</span>';
                setTimeout(() => {
                    macField.innerHTML = originalContent;
                }, 1000);
            });
        }
    };

    document.addEventListener('click', function(e) {
        const macField = e.target.closest('.field-tag_mac, .field-gateway_mac');
        if (macField && !e.target.closest('a')) {
            copyMacToClipboard(macField);
        }
    });

    // 7. SYNC BUTTON LOADING FEEDBACK
    document.addEventListener('click', function(e) {
        const syncBtn = e.target.closest('.btn-sync');
        if (syncBtn && !syncBtn.classList.contains('btn-loading')) {
            syncBtn.classList.add('btn-loading');
            const icon = syncBtn.querySelector('span[aria-hidden="true"]');
            if (icon) {
                icon.classList.add('spinning');
            }
            // Add a small delay for visual feedback before navigation if it's too fast
            // but usually redirects take enough time.
            // We just let the default navigation happen.
        }
    });

    // Add visual cue for copyable fields
    document.querySelectorAll('.field-tag_mac, .field-gateway_mac').forEach(el => {
        el.title = 'Click or press Enter/Space to copy MAC address';
        el.setAttribute('role', 'button');
        el.setAttribute('tabindex', '0');
        el.setAttribute('aria-label', `Copy MAC address: ${el.innerText.trim()}`);
    });

    const style = document.createElement('style');
    style.textContent = `
        .field-tag_mac, .field-gateway_mac {
            cursor: pointer;
            position: relative;
            transition: background-color 0.2s;
        }
        .field-tag_mac:focus-visible, .field-gateway_mac:focus-visible {
            outline: 2px solid var(--primary-blue, #2563eb);
            outline-offset: -2px;
        }
        .field-tag_mac:hover, .field-gateway_mac:hover {
            background-color: #f1f5f9 !important;
        }
        .field-tag_mac:active, .field-gateway_mac:active {
            background-color: #e2e8f0 !important;
        }
    `;
    document.head.appendChild(style);

    document.addEventListener('keydown', function(e) {
        // Rule: Ignore shortcuts if the user is currently typing in a text box
        const active = document.activeElement;
        if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.tagName === 'SELECT')) {
            return;
        }

        // '/' focuses the Search Bar
        if (e.key === '/' && searchInput && !e.ctrlKey && !e.metaKey) {
            e.preventDefault();
            searchInput.focus();
            searchInput.select();
        }

        // 'f' toggles the Filters sidebar
        if ((e.key === 'f' || e.key === 'F') && !e.ctrlKey && !e.metaKey) {
            const toggleBtn = document.getElementById('filter-toggle-btn');
            if (toggleBtn) {
                e.preventDefault();
                toggleBtn.click();
            }
        }

        // Enter or Space for MAC address copy
        const macField = e.target.closest('.field-tag_mac, .field-gateway_mac');
        if (macField && (e.key === 'Enter' || e.key === ' ')) {
            e.preventDefault();
            copyMacToClipboard(macField);
        }
    });
});
