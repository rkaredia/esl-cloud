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
        // Load initial state - default to hidden if not explicitly set to 'true'
        const filterState = localStorage.getItem('sais-admin-filter-visible');
        if (filterState !== 'true') {
            changelistWrapper.classList.add('filter-hidden');
        }

        // Add toggle logic to the button if it exists in the template
        const toggleBtn = document.getElementById('filter-toggle-btn');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', function(e) {
                e.preventDefault();
                changelistWrapper.classList.toggle('filter-hidden');
                const isVisible = !changelistWrapper.classList.contains('filter-hidden');
                localStorage.setItem('sais-admin-filter-visible', isVisible);
            });
        } else {
            // Add toggle button to object tools if not already present (for other models)
            const objectTools = document.querySelector('.object-tools');
            if (objectTools) {
                const toggleItem = document.createElement('li');
                const newToggleBtn = document.createElement('a');
                newToggleBtn.id = 'filter-toggle-btn';
                newToggleBtn.innerHTML = 'Filters 👁';
                newToggleBtn.href = 'javascript:void(0);';
                newToggleBtn.className = 'addlink';
                newToggleBtn.style.background = '#64748b';
                newToggleBtn.addEventListener('click', function() {
                    changelistWrapper.classList.toggle('filter-hidden');
                    const isVisible = !changelistWrapper.classList.contains('filter-hidden');
                    localStorage.setItem('sais-admin-filter-visible', isVisible);
                });
                toggleItem.appendChild(newToggleBtn);
                objectTools.insertBefore(toggleItem, objectTools.firstChild);
            }
        }
    }
});
