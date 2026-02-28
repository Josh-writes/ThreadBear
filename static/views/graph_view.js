/**
 * Graph View for ThreadBear
 * 
 * Force-directed graph visualization showing branches as nodes
 * and edges as connections. Uses Fruchterman-Reingold algorithm.
 */

const GraphView = {
    svg: null,
    container: null,
    nodes: [],
    links: [],

    render(container, data) {
        this.container = container;
        
        // Build controls
        const controls = this.buildControls(data);
        container.appendChild(controls);

        // Create SVG
        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.setAttribute('class', 'graph-svg');
        this.svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
        this.svg.style.width = '100%';
        this.svg.style.height = '500px';
        container.appendChild(this.svg);

        // Prepare nodes and links
        this.nodes = (data.nodes || []).map(n => ({
            ...n,
            x: Math.random() * 700 + 50,
            y: Math.random() * 400 + 50,
            vx: 0,
            vy: 0
        }));

        this.links = (data.edges || []).map(e => ({
            source: e.from_branch,
            target: e.to_branch,
            type: e.type,
            payload: e.payload
        }));

        // Run force simulation
        this.simulate();
        this.renderGraph();
    },

    buildControls(data) {
        const div = document.createElement('div');
        div.className = 'graph-controls';

        // Domain filter
        const domains = [...new Set(data.nodes.filter(n => n.type === 'domain').map(n => n.id))];
        if (domains.length > 0) {
            const domainFilter = document.createElement('select');
            domainFilter.className = 'graph-filter';
            domainFilter.innerHTML = '<option value="">All Domains</option>' +
                data.nodes.filter(n => n.type === 'domain')
                    .map(d => `<option value="${d.id}">${d.name}</option>`)
                    .join('');
            domainFilter.onchange = (e) => {
                this.refresh(data, e.target.value || null);
            };
            div.appendChild(domainFilter);
        }

        // Edge type toggles
        const edgeTypes = [...new Set(data.edges.map(e => e.type))];
        edgeTypes.forEach(type => {
            const label = document.createElement('label');
            label.className = 'graph-toggle';
            label.innerHTML = `<input type="checkbox" checked data-edge="${type}" /> ${type}`;
            label.querySelector('input').onchange = () => this.toggleEdgeType(type);
            div.appendChild(label);
        });

        // Reset button
        const resetBtn = document.createElement('button');
        resetBtn.className = 'graph-btn';
        resetBtn.textContent = 'Reset Layout';
        resetBtn.onclick = () => {
            this.nodes.forEach(n => {
                n.x = Math.random() * 700 + 50;
                n.y = Math.random() * 400 + 50;
                n.vx = 0;
                n.vy = 0;
            });
            this.simulate();
            this.renderGraph();
        };
        div.appendChild(resetBtn);

        return div;
    },

    toggleEdgeType(type) {
        const lines = this.svg.querySelectorAll(`.graph-edge.edge-${type}`);
        lines.forEach(line => {
            line.style.display = line.style.display === 'none' ? '' : 'none';
        });
    },

    refresh(data, domainFilter) {
        if (!this.container) return;
        this.container.innerHTML = '';
        
        // Filter nodes by domain
        let filteredNodes = data.nodes;
        if (domainFilter) {
            const domainIds = new Set([domainFilter]);
            data.nodes.forEach(n => {
                if (n.parent_id && domainIds.has(n.parent_id)) {
                    domainIds.add(n.id);
                }
            });
            filteredNodes = data.nodes.filter(n => domainIds.has(n.id) || !n.parent_id);
        }

        const filteredData = {
            nodes: filteredNodes,
            edges: data.edges
        };

        this.render(this.container, filteredData);
    },

    simulate() {
        const width = 800;
        const height = 500;
        const iterations = 100;
        const area = width * height;
        const k = Math.sqrt(area / Math.max(this.nodes.length, 1));

        for (let iter = 0; iter < iterations; iter++) {
            const temp = (1 - iter / iterations) * 10;

            // Repulsive forces (node-node)
            for (let i = 0; i < this.nodes.length; i++) {
                this.nodes[i].vx = 0;
                this.nodes[i].vy = 0;
                
                for (let j = i + 1; j < this.nodes.length; j++) {
                    const dx = this.nodes[i].x - this.nodes[j].x;
                    const dy = this.nodes[i].y - this.nodes[j].y;
                    const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
                    const force = (k * k) / dist;
                    
                    const fx = (dx / dist) * force;
                    const fy = (dy / dist) * force;
                    
                    this.nodes[i].vx += fx;
                    this.nodes[i].vy += fy;
                    this.nodes[j].vx -= fx;
                    this.nodes[j].vy -= fy;
                }
            }

            // Attractive forces (linked nodes)
            for (const link of this.links) {
                const source = this.nodes.find(n => n.id === link.source);
                const target = this.nodes.find(n => n.id === link.target);
                if (!source || !target) continue;

                const dx = source.x - target.x;
                const dy = source.y - target.y;
                const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
                const force = (dist * dist) / k;

                const fx = (dx / dist) * force;
                const fy = (dy / dist) * force;

                source.vx -= fx;
                source.vy -= fy;
                target.vx += fx;
                target.vy += fy;
            }

            // Apply forces with temperature
            for (const node of this.nodes) {
                const disp = Math.sqrt(node.vx * node.vx + node.vy * node.vy);
                if (disp > 0) {
                    node.x += (node.vx / disp) * Math.min(disp, temp);
                    node.y += (node.vy / disp) * Math.min(disp, temp);
                }
                // Keep in bounds
                node.x = Math.max(50, Math.min(width - 50, node.x));
                node.y = Math.max(50, Math.min(height - 50, node.y));
            }
        }
    },

    renderGraph() {
        this.svg.innerHTML = '';

        // Define arrow markers
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
        
        const edgeStyles = {
            parent_of: '#999',
            derived_from: '#999',
            depends_on: '#ff9800',
            artifact_flow: '#2196f3',
            merged_into: '#4caf50',
            references: '#ccc'
        };

        for (const [type, color] of Object.entries(edgeStyles)) {
            const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
            marker.setAttribute('id', `arrow-${type}`);
            marker.setAttribute('markerWidth', '10');
            marker.setAttribute('markerHeight', '10');
            marker.setAttribute('refX', '9');
            marker.setAttribute('refY', '3');
            marker.setAttribute('orient', 'auto');
            marker.setAttribute('markerUnits', 'strokeWidth');
            
            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', 'M0,0 L0,6 L9,3 z');
            path.setAttribute('fill', color);
            marker.appendChild(path);
            defs.appendChild(marker);
        }

        this.svg.appendChild(defs);

        // Draw edges
        for (const link of this.links) {
            const source = this.nodes.find(n => n.id === link.source);
            const target = this.nodes.find(n => n.id === link.target);
            if (!source || !target) continue;

            const line = this.createEdge(source, target, link.type);
            this.svg.appendChild(line);
        }

        // Draw nodes
        for (const node of this.nodes) {
            const group = this.createNode(node);
            this.svg.appendChild(group);
        }
    },

    createNode(node) {
        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('transform', `translate(${node.x}, ${node.y})`);
        g.setAttribute('class', `graph-node type-${node.type} status-${node.status}`);

        const w = Math.max(80, (node.name || '').length * 7 + 20);
        const h = 30;

        // Background rect
        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('x', -w/2);
        rect.setAttribute('y', -h/2);
        rect.setAttribute('width', w);
        rect.setAttribute('height', h);
        rect.setAttribute('rx', 6);

        const fill = {
            domain: 'var(--bg-secondary, #e3f2fd)',
            work_order: 'var(--bg-tertiary, #e8f5e9)',
            chat: 'var(--bg-primary, #f5f5f5)'
        }[node.type] || '#f5f5f5';

        const stroke = {
            domain: 'var(--link-color, #1976d2)',
            work_order: 'var(--success-color, #388e3c)',
            chat: 'var(--text-secondary, #999)'
        }[node.type] || '#999';

        rect.setAttribute('fill', fill);
        rect.setAttribute('stroke', stroke);
        rect.setAttribute('stroke-width', '2');

        // Border style by status
        const dashArray = {
            active: '',
            review: '5,3',
            merged: '2,2',
            archived: '1,3'
        }[node.status] || '';
        if (dashArray) rect.setAttribute('stroke-dasharray', dashArray);

        // Opacity for archived
        if (node.status === 'archived') {
            rect.setAttribute('opacity', '0.5');
        }

        // Label
        const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        text.textContent = node.name || 'Untitled';
        text.setAttribute('text-anchor', 'middle');
        text.setAttribute('dominant-baseline', 'central');
        text.setAttribute('font-size', '11');
        text.setAttribute('fill', 'var(--text-primary, #333)');

        g.appendChild(rect);
        g.appendChild(text);

        // Click to navigate
        g.style.cursor = 'pointer';
        g.onclick = () => ThreadBearViews.onBranchSelect(node.id);

        // Hover to highlight edges
        g.onmouseenter = () => this.highlightEdges(node.id);
        g.onmouseleave = () => this.unhighlightEdges();

        g.appendChild(this.makeDraggable(g, node));

        return g;
    },

    createEdge(source, target, type) {
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', source.x);
        line.setAttribute('y1', source.y);
        line.setAttribute('x2', target.x);
        line.setAttribute('y2', target.y);
        line.setAttribute('class', `graph-edge edge-${type}`);

        const styles = {
            parent_of:    { stroke: '#999', width: 1.5, dash: '' },
            derived_from: { stroke: '#999', width: 1, dash: '4,3' },
            depends_on:   { stroke: '#ff9800', width: 2, dash: '' },
            artifact_flow:{ stroke: '#2196f3', width: 2, dash: '3,3' },
            merged_into:  { stroke: '#4caf50', width: 3, dash: '' },
            references:   { stroke: '#ccc', width: 1, dash: '2,4' }
        }[type] || { stroke: '#999', width: 1, dash: '' };

        line.setAttribute('stroke', styles.stroke);
        line.setAttribute('stroke-width', styles.width);
        if (styles.dash) line.setAttribute('stroke-dasharray', styles.dash);
        line.setAttribute('marker-end', `url(#arrow-${type})`);

        return line;
    },

    highlightEdges(nodeId) {
        const edges = this.svg.querySelectorAll('.graph-edge');
        edges.forEach(edge => {
            const sourceId = this.links.find(l => 
                edge.getAttribute('x1') === this.nodes.find(n => n.id === l.source)?.x.toString()
            )?.source;
            const targetId = this.links.find(l => 
                edge.getAttribute('x2') === this.nodes.find(n => n.id === l.target)?.x.toString()
            )?.target;
            
            if (sourceId === nodeId || targetId === nodeId) {
                edge.classList.add('highlighted');
                edge.setAttribute('stroke-width', 
                    parseInt(edge.getAttribute('stroke-width')) + 2);
            }
        });
    },

    unhighlightEdges() {
        const edges = this.svg.querySelectorAll('.graph-edge');
        edges.forEach(edge => {
            edge.classList.remove('highlighted');
            // Reset stroke-width based on type
            const type = Array.from(edge.classList)
                .find(c => c.startsWith('edge-'))?.replace('edge-', '');
            const styles = {
                parent_of: 1.5, derived_from: 1, depends_on: 2,
                artifact_flow: 2, merged_into: 3, references: 1
            }[type] || 1;
            edge.setAttribute('stroke-width', styles);
        });
    },

    makeDraggable(g, node) {
        let isDragging = false;

        g.onmousedown = (e) => {
            isDragging = true;
            this.svg.style.cursor = 'grabbing';
        };

        this.svg.onmousemove = (e) => {
            if (!isDragging) return;
            const rect = this.svg.getBoundingClientRect();
            node.x = (e.clientX - rect.left) * (800 / rect.width);
            node.y = (e.clientY - rect.top) * (500 / rect.height);
            g.setAttribute('transform', `translate(${node.x}, ${node.y})`);
            
            // Update connected edges
            this.renderGraph();
        };

        this.svg.onmouseup = () => {
            isDragging = false;
            this.svg.style.cursor = '';
        };

        return g;
    },

    destroy() {
        if (this.svg) {
            this.svg.innerHTML = '';
        }
        this.container = null;
        this.nodes = [];
        this.links = [];
    }
};
