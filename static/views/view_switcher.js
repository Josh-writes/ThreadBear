/**
 * View Switcher for ThreadBear
 * 
 * Manages tab switching between Tree, Timeline, and Graph views.
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

        // Restore saved view
        const saved = localStorage.getItem('threadbear_view') || 'tree';
        this.switchView(saved);

        // Build tab bar
        this.buildTabBar();
    },

    buildTabBar() {
        const tabBar = document.getElementById('viewTabBar');
        if (!tabBar) return;

        tabBar.innerHTML = '';
        for (const [name, view] of Object.entries(this.views)) {
            const tab = document.createElement('button');
            tab.className = `view-tab ${name === this.activeView ? 'active' : ''}`;
            tab.textContent = `${view.icon} ${view.label}`;
            tab.onclick = () => this.switchView(name);
            tabBar.appendChild(tab);
        }
    },

    switchView(viewName) {
        if (!this.views[viewName]) return;

        // Deactivate current view
        if (this.activeView && this.views[this.activeView].module) {
            try {
                this.views[this.activeView].module.destroy();
            } catch (e) {
                console.warn('Error destroying view:', e);
            }
        }

        // Update tab states
        document.querySelectorAll('.view-tab').forEach(t => t.classList.remove('active'));
        const tabIndex = Object.keys(this.views).indexOf(viewName);
        const activeTab = document.querySelectorAll('.view-tab')[tabIndex];
        if (activeTab) activeTab.classList.add('active');

        // Activate new view
        this.activeView = viewName;
        localStorage.setItem('threadbear_view', viewName);

        // Clear and render
        const container = document.getElementById('viewContainer');
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

    // Called from chat.js when a branch is selected
    onBranchSelect(branchId) {
        // Navigate to that branch's chat
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
    }
};

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => ThreadBearViews.init());
} else {
    ThreadBearViews.init();
}
