/**
 * Tree View for ThreadBear
 * 
 * Enhanced tree visualization with artifact badges, edge indicators,
 * focus path highlighting, and filtering.
 */

const TreeView = {
    container: null,
    expandedNodes: new Set(),
    filters: {
        domain: null,
        hideArchived: false,
        hideMerged: false,
        collapseCompleted: false
    },

    render(container, data) {
        this.container = container;
        this.expandedNodes.clear();
        
        // Build controls
        const controls = this.buildControls(data);
        container.appendChild(controls);

        // Apply filters
        const filtered = this.applyFilters(data.nodes, data.edges);

        // Build tree
        const tree = this.buildTree(filtered.nodes, filtered.edges);
        container.appendChild(tree);
    },

    buildControls(data) {
        const div = document.createElement('div');
        div.className = 'tree-controls';

        // Domain filter
        const domains = [...new Set(data.nodes.filter(n => n.type === 'domain').map(n => n.id))];
        if (domains.length > 0) {
            const domainFilter = document.createElement('select');
            domainFilter.className = 'tree-filter';
            domainFilter.innerHTML = '<option value="">All Domains</option>' +
                data.nodes.filter(n => n.type === 'domain')
                    .map(d => `<option value="${d.id}">${d.name}</option>`)
                    .join('');
            domainFilter.onchange = (e) => {
                this.filters.domain = e.target.value || null;
                this.refresh(data);
            };
            div.appendChild(domainFilter);
        }

        // Status toggles
        const hideArchived = document.createElement('label');
        hideArchived.className = 'tree-toggle';
        hideArchived.innerHTML = '<input type="checkbox" /> Hide Archived';
        hideArchived.querySelector('input').onchange = (e) => {
            this.filters.hideArchived = e.target.checked;
            this.refresh(data);
        };
        div.appendChild(hideArchived);

        const hideMerged = document.createElement('label');
        hideMerged.className = 'tree-toggle';
        hideMerged.innerHTML = '<input type="checkbox" /> Hide Merged';
        hideMerged.querySelector('input').onchange = (e) => {
            this.filters.hideMerged = e.target.checked;
            this.refresh(data);
        };
        div.appendChild(hideMerged);

        const collapseCompleted = document.createElement('label');
        collapseCompleted.className = 'tree-toggle';
        collapseCompleted.innerHTML = '<input type="checkbox" /> Collapse Completed';
        collapseCompleted.querySelector('input').onchange = (e) => {
            this.filters.collapseCompleted = e.target.checked;
            this.refresh(data);
        };
        div.appendChild(collapseCompleted);

        return div;
    },

    applyFilters(nodes, edges) {
        let filtered = nodes;

        // Domain filter
        if (this.filters.domain) {
            const domainIds = new Set([this.filters.domain]);
            // Include all children of the domain
            nodes.forEach(n => {
                if (n.parent_id && domainIds.has(n.parent_id)) {
                    domainIds.add(n.id);
                }
            });
            filtered = filtered.filter(n => domainIds.has(n.id) || !n.parent_id);
        }

        // Status filters
        if (this.filters.hideArchived) {
            filtered = filtered.filter(n => n.status !== 'archived');
        }
        if (this.filters.hideMerged) {
            filtered = filtered.filter(n => n.status !== 'merged');
        }

        return { nodes: filtered, edges };
    },

    refresh(data) {
        if (!this.container) return;
        this.container.innerHTML = '';
        this.render(this.container, data);
    },

    buildTree(nodes, edges) {
        // Build parent_id -> children map
        const childrenMap = {};
        const roots = [];

        for (const node of nodes) {
            if (node.parent_id) {
                (childrenMap[node.parent_id] = childrenMap[node.parent_id] || []).push(node);
            } else {
                roots.push(node);
            }
        }

        // Sort roots by created_at
        roots.sort((a, b) => a.created_at.localeCompare(b.created_at));

        const treeEl = document.createElement('div');
        treeEl.className = 'tree-view';

        for (const root of roots) {
            treeEl.appendChild(this.buildNode(root, childrenMap, edges, 0));
        }

        return treeEl;
    },

    buildNode(node, childrenMap, edges, depth) {
        const el = document.createElement('div');
        el.className = `tree-node depth-${depth} status-${node.status}`;
        el.dataset.branchId = node.id;

        const icon = {domain: '📁', work_order: '📋', chat: '💬'}[node.type] || '📄';
        const statusBadge = {active: '●', review: '🟡', merged: '✓', archived: '○'}[node.status] || '';
        const artifactCount = node.artifact_count || 0;

        el.innerHTML = `
            <span class="tree-toggle">${depth === 0 ? '▼' : '  '}</span>
            <span class="tree-icon">${icon}</span>
            <span class="tree-name">${node.name || 'Untitled'}</span>
            <span class="tree-status" title="${node.status}">${statusBadge}</span>
            ${artifactCount > 0 ? `<span class="tree-artifacts" title="${artifactCount} artifacts">●${artifactCount}</span>` : ''}
        `;

        // Click to navigate
        el.querySelector('.tree-name').onclick = (e) => {
            e.stopPropagation();
            ThreadBearViews.onBranchSelect(node.id);
        };

        // Non-parent edges (depends_on, artifact_flow, etc.)
        const relatedEdges = edges.filter(e =>
            (e.from_branch === node.id || e.to_branch === node.id) && e.type !== 'parent_of'
        );
        if (relatedEdges.length > 0) {
            const edgeList = document.createElement('div');
            edgeList.className = 'tree-edges';
            for (const edge of relatedEdges) {
                const indicator = document.createElement('div');
                indicator.className = `tree-edge-indicator edge-${edge.type}`;
                const direction = edge.from_branch === node.id ? '→' : '←';
                const otherId = edge.from_branch === node.id ? edge.to_branch : edge.from_branch;
                indicator.textContent = `${direction} ${edge.type}`;
                indicator.title = `${edge.type}: ${otherId.substring(0, 8)}`;
                edgeList.appendChild(indicator);
            }
            el.appendChild(edgeList);
        }

        // Children
        const children = (childrenMap[node.id] || []).sort((a, b) => 
            a.created_at.localeCompare(b.created_at)
        );
        
        if (children.length > 0) {
            const childContainer = document.createElement('div');
            childContainer.className = 'tree-children';
            
            // Auto-collapse completed if filter enabled
            if (this.filters.collapseCompleted && 
                (node.status === 'merged' || node.status === 'archived')) {
                childContainer.classList.add('collapsed');
                el.querySelector('.tree-toggle').textContent = '▶';
            }

            for (const child of children) {
                childContainer.appendChild(this.buildNode(child, childrenMap, edges, depth + 1));
            }
            el.appendChild(childContainer);

            // Toggle collapse
            el.querySelector('.tree-toggle').onclick = (e) => {
                e.stopPropagation();
                childContainer.classList.toggle('collapsed');
                el.querySelector('.tree-toggle').textContent = 
                    childContainer.classList.contains('collapsed') ? '▶' : '▼';
            };
        }

        return el;
    },

    destroy() {
        this.container = null;
        this.expandedNodes.clear();
    }
};
