/**
 * Timeline View for ThreadBear
 * 
 * Horizontal SVG timeline showing branch lanes, creation events,
 * status transitions, and artifact flows.
 */

const TimelineView = {
    svg: null,
    container: null,

    render(container, data) {
        this.container = container;
        
        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.setAttribute('class', 'timeline-svg');
        this.svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
        container.appendChild(this.svg);

        this.renderTimeline(data);
    },

    renderTimeline(data) {
        const branches = data.nodes || [];
        const edges = data.edges || [];

        if (branches.length === 0) {
            this.svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" class="empty-message">No branches to display</text>';
            return;
        }

        // Calculate time range
        const times = branches.map(b => new Date(b.created_at).getTime());
        let minTime = Math.min(...times);
        const maxTime = Math.max(...times, Date.now());
        
        // Add padding
        minTime -= 86400000; // 1 day before
        const timeRange = maxTime - minTime || 1;

        // Layout constants
        const LANE_HEIGHT = 32;
        const LEFT_MARGIN = 160;
        const RIGHT_MARGIN = 40;
        const TOP_MARGIN = 50;
        const BOTTOM_MARGIN = 60;
        const TIMELINE_WIDTH = 800;

        // Assign lanes
        const lanes = this.assignLanes(branches);
        const svgHeight = TOP_MARGIN + lanes.length * LANE_HEIGHT + BOTTOM_MARGIN;
        const svgWidth = LEFT_MARGIN + TIMELINE_WIDTH + RIGHT_MARGIN;

        this.svg.setAttribute('viewBox', `0 0 ${svgWidth} ${svgHeight}`);
        this.svg.style.width = '100%';
        this.svg.style.minHeight = `${svgHeight}px`;

        // Draw time axis
        this.drawTimeAxis(minTime, maxTime, TOP_MARGIN - 10, svgWidth - RIGHT_MARGIN, LEFT_MARGIN);

        // Draw each branch lane
        for (let idx = 0; idx < lanes.length; idx++) {
            const { branch, depth } = lanes[idx];
            const y = TOP_MARGIN + idx * LANE_HEIGHT;
            this.drawBranchLane(branch, depth, y, idx, lanes.length, minTime, timeRange, LEFT_MARGIN, TIMELINE_WIDTH, edges);
        }

        // Draw artifact flow arrows
        for (const edge of edges.filter(e => e.type === 'artifact_flow')) {
            this.drawArtifactArrow(edge, lanes, minTime, timeRange, LEFT_MARGIN, TOP_MARGIN, LANE_HEIGHT);
        }
    },

    assignLanes(branches) {
        const lanes = [];
        // Group by domain, then children
        const roots = branches.filter(b => !b.parent_id)
            .sort((a, b) => a.created_at.localeCompare(b.created_at));
        
        for (const root of roots) {
            lanes.push({ branch: root, depth: 0 });
            const children = branches.filter(b => b.parent_id === root.id)
                .sort((a, b) => a.created_at.localeCompare(b.created_at));
            for (const child of children) {
                lanes.push({ branch: child, depth: 1 });
            }
        }
        return lanes;
    },

    timeToX(time, minTime, range, width) {
        return ((time - minTime) / range) * width;
    },

    drawTimeAxis(minTime, maxTime, y, width, leftMargin) {
        const axis = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        axis.setAttribute('class', 'time-axis');

        // Axis line
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', leftMargin);
        line.setAttribute('y1', y);
        line.setAttribute('x2', width);
        line.setAttribute('y2', y);
        line.setAttribute('stroke', 'var(--border-color)');
        line.setAttribute('stroke-width', '1');
        axis.appendChild(line);

        // Time labels (5 intervals)
        const intervals = 5;
        const range = maxTime - minTime;
        for (let i = 0; i <= intervals; i++) {
            const time = minTime + (range * i / intervals);
            const x = leftMargin + this.timeToX(time, minTime, range, width - leftMargin);
            const date = new Date(time);
            const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            label.setAttribute('x', x);
            label.setAttribute('y', y - 5);
            label.setAttribute('text-anchor', 'middle');
            label.setAttribute('class', 'time-label');
            label.textContent = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            axis.appendChild(label);

            // Tick mark
            const tick = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            tick.setAttribute('x1', x);
            tick.setAttribute('y1', y);
            tick.setAttribute('x2', x);
            tick.setAttribute('y2', y + 5);
            tick.setAttribute('stroke', 'var(--border-color)');
            tick.setAttribute('stroke-width', '1');
            axis.appendChild(tick);
        }

        this.svg.appendChild(axis);
    },

    drawBranchLane(branch, depth, y, idx, totalLanes, minTime, timeRange, leftMargin, timelineWidth, edges) {
        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('class', `branch-lane depth-${depth}`);
        g.setAttribute('transform', `translate(0, ${y})`);

        // Label
        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('x', depth * 12 + 5);
        label.setAttribute('y', 16);
        label.setAttribute('class', `lane-label depth-${depth}`);
        label.textContent = branch.name || 'Untitled';
        label.style.cursor = 'pointer';
        label.onclick = () => ThreadBearViews.onBranchSelect(branch.id);
        g.appendChild(label);

        // Branch line
        const x1 = leftMargin + this.timeToX(new Date(branch.created_at).getTime(), minTime, timeRange, timelineWidth);
        const isComplete = branch.status === 'merged' || branch.status === 'archived';
        const x2 = isComplete 
            ? leftMargin + this.timeToX(new Date(branch.updated_at || branch.created_at).getTime(), minTime, timeRange, timelineWidth)
            : leftMargin + timelineWidth;

        const color = {
            domain: 'var(--link-color, #4a9eff)',
            work_order: 'var(--success-color, #4caf50)',
            chat: 'var(--text-secondary, #999)'
        }[branch.type] || '#999';

        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', x1);
        line.setAttribute('y1', 12);
        line.setAttribute('x2', x2);
        line.setAttribute('y2', 12);
        line.setAttribute('stroke', color);
        line.setAttribute('stroke-width', depth > 0 ? '2' : '3');
        line.setAttribute('stroke-linecap', 'round');
        
        // Dashed for merged/archived
        if (branch.status === 'merged') {
            line.setAttribute('stroke-dasharray', '5,3');
        } else if (branch.status === 'archived') {
            line.setAttribute('stroke-dasharray', '2,2');
            line.setAttribute('opacity', '0.5');
        }
        
        g.appendChild(line);

        // Creation dot
        const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        dot.setAttribute('cx', x1);
        dot.setAttribute('cy', 12);
        dot.setAttribute('r', '4');
        dot.setAttribute('fill', color);
        dot.setAttribute('class', 'timeline-event');
        const createdDate = new Date(branch.created_at).toLocaleDateString();
        dot.innerHTML = `<title>Created: ${branch.name}\n${createdDate}</title>`;
        dot.style.cursor = 'pointer';
        dot.onclick = () => ThreadBearViews.onBranchSelect(branch.id);
        g.appendChild(dot);

        // Status marker for merged
        if (branch.status === 'merged') {
            const mergeMarker = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            mergeMarker.setAttribute('x', x2 + 5);
            mergeMarker.setAttribute('y', 16);
            mergeMarker.setAttribute('class', 'status-marker');
            mergeMarker.textContent = '✓';
            mergeMarker.setAttribute('fill', color);
            g.appendChild(mergeMarker);
        }

        this.svg.appendChild(g);
    },

    drawArtifactArrow(edge, lanes, minTime, timeRange, leftMargin, topMargin, laneHeight) {
        const fromLane = lanes.findIndex(l => l.branch.id === edge.from_branch);
        const toLane = lanes.findIndex(l => l.branch.id === edge.to_branch);
        
        if (fromLane === -1 || toLane === -1) return;

        const fromBranch = lanes[fromLane].branch;
        const toBranch = lanes[toLane].branch;

        const x1 = leftMargin + this.timeToX(new Date(fromBranch.created_at).getTime(), minTime, timeRange, 800);
        const x2 = leftMargin + this.timeToX(new Date(toBranch.created_at).getTime(), minTime, timeRange, 800);
        const y1 = topMargin + fromLane * laneHeight + 12;
        const y2 = topMargin + toLane * laneHeight + 12;

        // Draw dotted arrow
        const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        arrow.setAttribute('x1', x1);
        arrow.setAttribute('y1', y1);
        arrow.setAttribute('x2', x2);
        arrow.setAttribute('y2', y2);
        arrow.setAttribute('stroke', 'var(--link-color, #2196f3)');
        arrow.setAttribute('stroke-width', '1.5');
        arrow.setAttribute('stroke-dasharray', '3,3');
        arrow.setAttribute('class', 'artifact-flow-arrow');
        
        // Add arrowhead
        const angle = Math.atan2(y2 - y1, x2 - x1);
        const arrowSize = 6;
        const arrowX = x2 - arrowSize * Math.cos(angle);
        const arrowY = y2 - arrowSize * Math.sin(angle);
        
        const arrowhead = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        arrowhead.setAttribute('x1', arrowX);
        arrowhead.setAttribute('y1', arrowY);
        arrowhead.setAttribute('x2', x2);
        arrowhead.setAttribute('y2', y2);
        arrowhead.setAttribute('stroke', 'var(--link-color, #2196f3)');
        arrowhead.setAttribute('stroke-width', '1.5');
        
        this.svg.appendChild(arrow);
        this.svg.appendChild(arrowhead);
    },

    destroy() {
        if (this.svg) {
            this.svg.innerHTML = '';
        }
        this.container = null;
    }
};
