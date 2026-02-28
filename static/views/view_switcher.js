/**
 * View Switcher for ThreadBear
 *
 * Manages sidebar-based Tree, Timeline, and Graph views.
 * Each view is a standalone module with render() and destroy() methods.
 */

const ThreadBearViews = {
    activeView: null,
    views: {
        tree:     { module: null, label: 'Tree',     icon: '🌲' },
        timeline: { module: null, label: 'Timeline', icon: '📅' },
        graph:    { module: null, label: 'Graph',    icon: '🕸️' }
    },

    init() {
        // Load view modules
        this.views.tree.module = TreeView;
        this.views.timeline.module = TimelineView;
        this.views.graph.module = GraphView;

        // Setup toggle button listeners
        this.setupToggleListeners();

        // Setup close button
        const closeBtn = document.getElementById('sidebarViewCloseBtn');
        if (closeBtn) {
            closeBtn.onclick = () => this.closePanel();
        }

        // Restore saved view if any
        const saved = localStorage.getItem('threadbear_view');
        if (saved && this.views[saved]) {
            this.openPanel(saved);
        }
    },

    setupToggleListeners() {
        const toggles = document.querySelectorAll('.view-toggle-btn');
        toggles.forEach(btn => {
            btn.onclick = () => {
                const viewName = btn.getAttribute('data-view');
                if (this.activeView === viewName) {
                    // Toggle off if already active
                    this.closePanel();
                } else {
                    this.openPanel(viewName);
                }
            };
        });
    },

    openPanel(viewName) {
        if (!this.views[viewName]) return;

        // Update toggle button states
        document.querySelectorAll('.view-toggle-btn').forEach(btn => {
            btn.classList.toggle('active', btn.getAttribute('data-view') === viewName);
        });

        // Update panel title
        const titleEl = document.getElementById('sidebarViewTitle');
        if (titleEl) {
            titleEl.textContent = `${this.views[viewName].icon} ${this.views[viewName].label} View`;
        }

        // Show panel
        const panel = document.getElementById('sidebarViewPanel');
        if (panel) {
            panel.classList.add('open');
        }

        // Deactivate current view if different
        if (this.activeView && this.activeView !== viewName && this.views[this.activeView].module) {
            try {
                this.views[this.activeView].module.destroy();
            } catch (e) {
                console.warn('Error destroying view:', e);
            }
        }

        // Activate new view
        this.activeView = viewName;
        localStorage.setItem('threadbear_view', viewName);

        // Clear and render
        const container = document.getElementById('sidebarViewContent');
        if (!container) return;
        container.innerHTML = '';

        // Fetch graph data and render
        fetch('/api/graph')
            .then(r => r.json())
            .then(data => {
                try {
                    this.views[viewName].module.render(container, data);
                } catch (e) {
                    console.error(`Error rendering ${viewName} view:`, e);
                    container.innerHTML = `<div class="error">Failed to load ${viewName} view: ${e.message}</div>`;
                }
            })
            .catch(e => {
                console.error('Failed to fetch graph data:', e);
                container.innerHTML = '<div class="error">Failed to load view data</div>';
            });
    },

    closePanel() {
        // Deactivate current view
        if (this.activeView && this.views[this.activeView].module) {
            try {
                this.views[this.activeView].module.destroy();
            } catch (e) {
                console.warn('Error destroying view:', e);
            }
        }

        // Hide panel
        const panel = document.getElementById('sidebarViewPanel');
        if (panel) {
            panel.classList.remove('open');
        }

        // Reset toggle buttons
        document.querySelectorAll('.view-toggle-btn').forEach(btn => {
            btn.classList.remove('active');
        });

        this.activeView = null;
        localStorage.removeItem('threadbear_view');
    },

    // Called from chat.js when a branch is selected
    onBranchSelect(branchId) {
        // Navigate to that branch chat
        if (typeof loadChat === 'function') {
            // Find the filename for this branch ID
            fetch(`/api/branches/${branchId}`)
                .then(r => r.json())
                .then(data => {
                    if (data.success && data.branch.filename) {
                        loadChat(data.branch.filename);
                    }
                });
        }
    },

    // Refresh current view (call after branch operations)
    refresh() {
        if (this.activeView) {
            const container = document.getElementById('sidebarViewContent');
            if (container) {
                container.innerHTML = '';
                fetch('/api/graph')
                    .then(r => r.json())
                    .then(data => {
                        this.views[this.activeView].module.render(container, data);
                    })
                    .catch(e => {
                        console.error('Failed to refresh view:', e);
                    });
            }
        }
    }
};

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => ThreadBearViews.init());
} else {
    ThreadBearViews.init();
}
