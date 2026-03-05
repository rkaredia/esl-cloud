(function() {
    const run = () => {
        try {
            // --- 1. FILTER TOGGLE LOGIC ---
            const filter = document.getElementById('changelist-filter');
            // Target the existing button row specifically
            const toolbar = document.querySelector('.object-tools');

            if (filter && toolbar && !document.getElementById('toggle-filters-btn')) {
                const li = document.createElement('li');
                const btn = document.createElement('a'); // Changed to 'a' to match Django's style

                btn.id = 'toggle-filters-btn';
                btn.href = 'javascript:void(0)';
                btn.textContent = 'Show Filters';

                // Styling to match your existing "Add" buttons perfectly
                btn.style.cssText = 'background: #2563eb; color: white; padding: 8px 16px; border-radius: 4px; font-weight: bold; text-decoration: none; display: inline-block; margin-right: 10px;';

                li.appendChild(btn);
                toolbar.prepend(li); // Adds it to the start of the button list
                filter.classList.add('filter-hidden');

                btn.onclick = function() {
                    const isHidden = filter.classList.contains('filter-hidden');
                    if (isHidden) {
                        filter.classList.remove('filter-hidden');
                        this.textContent = 'Hide Filters';
                        this.style.background = '#dc2626';
                    } else {
                        filter.classList.add('filter-hidden');
                        this.textContent = 'Show Filters';
                        this.style.background = '#2563eb';
                    }
                };
            }

            // --- 2. TIMEZONE CONVERSION (Keep your working code here) ---
            document.querySelectorAll('.local-datetime').forEach(el => {
                if (el.dataset.utc && !el.dataset.done) {
                    const date = new Date(el.dataset.utc);
                    el.textContent = date.toLocaleString(undefined, {
                        month: 'short', day: 'numeric', year: 'numeric',
                        hour: '2-digit', minute: '2-digit', hour12: true
                    });
                    el.dataset.done = "true";
                }
            });

            // --- 3. TOP SCROLLBAR (Keep your working code here) ---
            const results = document.querySelector('.results');
            const table = results ? results.querySelector('table') : null;
            if (results && table && !document.querySelector('.top-scrollbar')) {
                const topScroll = document.createElement('div');
                topScroll.className = 'top-scrollbar';
                topScroll.style.cssText = 'overflow-x:auto; overflow-y:hidden; width:100%; height:12px; background:#f8f9fa; border-bottom:1px solid #dee2e6; margin-bottom: 2px;';
                const dummy = document.createElement('div');
                dummy.style.height = '1px';
                dummy.style.width = table.offsetWidth + 'px';
                topScroll.appendChild(dummy);
                results.parentNode.insertBefore(topScroll, results);
                topScroll.onscroll = () => { results.scrollLeft = topScroll.scrollLeft; };
                results.onscroll = () => { topScroll.scrollLeft = results.scrollLeft; };
                new ResizeObserver(() => { dummy.style.width = table.offsetWidth + 'px'; }).observe(table);
            }

        } catch (e) {
            console.error("Admin Enhancement Error: ", e);
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', run);
    } else {
        run();
    }
    new MutationObserver(run).observe(document.body, { childList: true, subtree: true });
})();