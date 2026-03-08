/* static/admin/js/sais_admin.js */

document.addEventListener('DOMContentLoaded', function() {
    // 1. Column Resizing Logic
    const table = document.getElementById('result_list');
    if (table) {
        const headerRow = table.querySelector('thead tr');
        if (headerRow) {
            const cols = headerRow.querySelectorAll('th');
            const modelMatch = document.body.className.match(/model-(\w+)/);
            const modelName = modelMatch ? modelMatch[1] : 'unknown';
            const storedWidths = JSON.parse(localStorage.getItem(`sais-admin-cols-${modelName}`) || '{}');

            cols.forEach((col, index) => {
                // Apply stored width
                if (storedWidths[index]) {
                    col.style.width = storedWidths[index] + 'px';
                }

                // Add resizer handle
                const resizer = document.createElement('div');
                resizer.classList.add('resizer');
                resizer.title = 'Drag to resize column';
                col.appendChild(resizer);

                let x = 0;
                let w = 0;

                const mouseDownHandler = function(e) {
                    x = e.clientX;
                    w = parseInt(window.getComputedStyle(col).width, 10);
                    document.addEventListener('mousemove', mouseMoveHandler);
                    document.addEventListener('mouseup', mouseUpHandler);
                    resizer.classList.add('resizing');
                };

                const mouseMoveHandler = function(e) {
                    const dx = e.clientX - x;
                    col.style.width = `${w + dx}px`;
                };

                const mouseUpHandler = function() {
                    document.removeEventListener('mousemove', mouseMoveHandler);
                    document.removeEventListener('mouseup', mouseUpHandler);
                    resizer.classList.remove('resizing');

                    // Store new width
                    const newWidths = JSON.parse(localStorage.getItem(`sais-admin-cols-${modelName}`) || '{}');
                    newWidths[index] = parseInt(col.style.width, 10);
                    localStorage.setItem(`sais-admin-cols-${modelName}`, JSON.stringify(newWidths));
                };

                resizer.addEventListener('mousedown', mouseDownHandler);
            });
        }
    }

    // 2. Filter Toggling Logic
    const changelistWrapper = document.getElementById('changelist-wrapper');
    const filter = document.getElementById('changelist-filter');
    if (changelistWrapper && filter) {
        const updateToggleButton = (btn, isVisible) => {
            if (!btn) return;
            btn.innerHTML = isVisible ? 'Hide Filters 👁' : 'Show Filters 👁';
            btn.setAttribute('aria-expanded', isVisible);
        };

        // Load initial state - default to hidden if not explicitly set to 'true'
        const filterState = localStorage.getItem('sais-admin-filter-visible');
        const isInitiallyVisible = filterState === 'true';

        if (!isInitiallyVisible) {
            changelistWrapper.classList.add('filter-hidden');
        }

        // Add toggle logic to the button if it exists in the template
        let toggleBtn = document.getElementById('filter-toggle-btn') || document.getElementById('toggle-filters');

        if (!toggleBtn) {
            // Add toggle button to object tools if not already present (for other models)
            const objectTools = document.querySelector('.object-tools');
            if (objectTools) {
                const toggleItem = document.createElement('li');
                toggleBtn = document.createElement('a');
                toggleBtn.id = 'filter-toggle-btn';
                toggleBtn.href = 'javascript:void(0);';
                toggleBtn.className = 'addlink';
                toggleBtn.style.background = '#64748b';
                toggleBtn.setAttribute('aria-controls', 'changelist-filter');
                toggleItem.appendChild(toggleBtn);
                objectTools.insertBefore(toggleItem, objectTools.firstChild);
            }
        }

        if (toggleBtn) {
            updateToggleButton(toggleBtn, isInitiallyVisible);
            toggleBtn.addEventListener('click', function(e) {
                e.preventDefault();
                changelistWrapper.classList.toggle('filter-hidden');
                const isVisible = !changelistWrapper.classList.contains('filter-hidden');
                localStorage.setItem('sais-admin-filter-visible', isVisible);
                updateToggleButton(toggleBtn, isVisible);
            });
        }
    }

    // 3. Store Selection Logic (Security: avoid inline onchange for XSS prevention)
    const storeSelect = document.getElementById('header-store-select');
    if (storeSelect) {
        storeSelect.addEventListener('change', function() {
            if (this.value) {
                window.location.href = '/set-store/' + encodeURIComponent(this.value) + '/';
            }
        });
    }

    // 4. MQTT Live Refresh Logic
    // Using a more robust check for the model name
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
                    // Use AJAX/fetch to check for new messages or just reload
                    // For simplicity, we reload the page, but preserving filters
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

        // Add copy buttons to payloads
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
});
