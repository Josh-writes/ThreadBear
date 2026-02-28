"""
Branch Manager for ThreadBear

High-level business logic for branch DAG operations.
Enforces branch type rules, lifecycle transitions, and edge semantics.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from branch_db import BranchDatabase


class BranchManager:
    """
    Business logic layer for branch/edge operations.
    Wraps BranchDatabase with enforcement of type rules, lifecycle, and semantics.
    """

    # Valid status transitions: current -> [allowed next states]
    VALID_TRANSITIONS = {
        'active': ['review', 'archived'],
        'review': ['active', 'merged', 'archived'],
        'merged': ['archived'],
        'archived': ['active'],  # reopen
    }

    # Branch types
    TYPE_CHAT = 'chat'
    TYPE_DOMAIN = 'domain'
    TYPE_WORK_ORDER = 'work_order'

    # Edge types
    EDGE_PARENT_OF = 'parent_of'
    EDGE_DERIVED_FROM = 'derived_from'
    EDGE_DEPENDS_ON = 'depends_on'
    EDGE_MERGED_INTO = 'merged_into'
    EDGE_REFERENCES = 'references'
    EDGE_ARTIFACT_FLOW = 'artifact_flow'  # Phase 5

    def __init__(self, db: BranchDatabase):
        self.db = db

    # ==================== Branch Creation ====================

    def create_domain_branch(self, name: str, description: str = '',
                             context_pack_id: Optional[str] = None,
                             policy: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Create a persistent domain branch (Research, Dev, Visual, etc.)
        Domain branches define context + tool permissions for their children.
        Always at top level (no parent_id).
        """
        now = datetime.now(timezone.utc).isoformat()
        branch = self.db.upsert_branch(
            branch_id=self._generate_id(),
            title=name,
            type=self.TYPE_DOMAIN,
            parent_id=None,
            root_id=None,
            status='active',
            context_pack_id=context_pack_id,
            policy=json.dumps(policy or {}),
            metadata=json.dumps({
                'description': description,
                'status_history': [{
                    'from': None,
                    'to': 'active',
                    'timestamp': now
                }]
            })
        )
        return self.db.get_branch(branch['id'])

    def create_work_order(self, parent_id: str, name: str, goal: str,
                          tools_allowed: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Create a scoped task branch under a domain branch.
        Work orders have goals and produce artifacts.
        Inherits policy from parent domain if not specified.
        """
        parent = self.db.get_branch(parent_id)
        if not parent:
            raise ValueError(f"Parent branch {parent_id} not found")

        # Inherit policy from parent domain if not specified
        policy = tools_allowed or json.loads(parent.get('policy', '{}'))
        root_id = parent.get('root_id') or parent_id

        now = datetime.now(timezone.utc).isoformat()
        branch_id = self._generate_id()
        branch = self.db.upsert_branch(
            branch_id=branch_id,
            title=name,
            type=self.TYPE_WORK_ORDER,
            parent_id=parent_id,
            root_id=root_id,
            status='active',
            policy=json.dumps(policy),
            metadata=json.dumps({
                'goal': goal,
                'status_history': [{
                    'from': None,
                    'to': 'active',
                    'timestamp': now
                }],
                'iteration_count': 0
            })
        )

        # Create parent_of edge
        self.db.add_edge(parent_id, branch_id, self.EDGE_PARENT_OF)

        return self.db.get_branch(branch_id)

    def create_chat_branch(self, title: str = 'New Chat',
                           parent_id: Optional[str] = None,
                           root_id: Optional[str] = None,
                           filename: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a regular chat branch.
        If parent_id is provided, it's a fork/side-chat.
        """
        now = datetime.now(timezone.utc).isoformat()
        branch_id = self._generate_id()

        # If no root_id provided and this is a child, use parent's root
        if not root_id and parent_id:
            parent = self.db.get_branch(parent_id)
            if parent:
                root_id = parent.get('root_id') or parent_id

        branch = self.db.upsert_branch(
            branch_id=branch_id,
            title=title,
            type=self.TYPE_CHAT,
            parent_id=parent_id,
            root_id=root_id or branch_id,
            status='active',
            filename=filename,
            metadata=json.dumps({
                'status_history': [{
                    'from': None,
                    'to': 'active',
                    'timestamp': now
                }]
            })
        )

        # If has parent, create derived_from edge (fork semantics)
        if parent_id:
            self.db.add_edge(parent_id, branch_id, self.EDGE_DERIVED_FROM, {
                'created_at': now
            })

        return self.db.get_branch(branch_id)

    def fork_branch(self, source_id: str, at_message_index: Optional[int] = None,
                    name: Optional[str] = None) -> Dict[str, Any]:
        """
        Fork a new branch from an existing one.
        Replaces the old create_side_chat mechanism.
        Creates a 'derived_from' edge with fork metadata.
        """
        source = self.db.get_branch(source_id)
        if not source:
            raise ValueError(f"Source branch {source_id} not found")

        fork_name = name or f"Fork of {source['title']}"
        now = datetime.now(timezone.utc).isoformat()

        fork_id = self._generate_id()
        fork = self.db.upsert_branch(
            branch_id=fork_id,
            title=fork_name,
            type=source.get('type', self.TYPE_CHAT),
            parent_id=source.get('parent_id'),
            root_id=source.get('root_id') or source_id,
            status='active',
            metadata=json.dumps({
                'forked_from': source_id,
                'fork_message_index': at_message_index,
                'status_history': [{
                    'from': None,
                    'to': 'active',
                    'timestamp': now
                }]
            })
        )

        self.db.add_edge(source_id, fork_id, self.EDGE_DERIVED_FROM, {
            'message_index': at_message_index,
            'created_at': now
        })

        return self.db.get_branch(fork_id)

    # ==================== Lifecycle Management ====================

    def transition_status(self, branch_id: str, new_status: str) -> Dict[str, Any]:
        """
        Enforce lifecycle: active → review → merged → archived.
        Records transition in metadata.status_history.
        """
        branch = self.db.get_branch(branch_id)
        if not branch:
            raise ValueError(f"Branch {branch_id} not found")

        current = branch.get('status', 'active')
        allowed = self.VALID_TRANSITIONS.get(current, [])

        if new_status not in allowed:
            raise ValueError(
                f"Cannot transition from '{current}' to '{new_status}'. "
                f"Allowed: {allowed}"
            )

        # Record in status history
        meta = json.loads(branch.get('metadata', '{}'))
        history = meta.get('status_history', [])
        history.append({
            'from': current,
            'to': new_status,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        meta['status_history'] = history

        self.db.upsert_branch(
            branch_id=branch_id,
            status=new_status,
            metadata=json.dumps(meta)
        )

        return self.db.get_branch(branch_id)

    def merge_branch(self, source_id: str, target_id: str,
                     approval_notes: str = '') -> Dict[str, Any]:
        """
        Merge a branch into another.
        Creates merged_into edge, transitions source to 'merged'.
        Source must be in 'review' status.
        """
        source = self.db.get_branch(source_id)
        if not source:
            raise ValueError(f"Source branch {source_id} not found")

        if source.get('status') != 'review':
            raise ValueError(
                f"Branch must be in 'review' status to merge "
                f"(currently '{source.get('status')}')"
            )

        now = datetime.now(timezone.utc).isoformat()
        self.db.add_edge(source_id, target_id, self.EDGE_MERGED_INTO, {
            'approval_notes': approval_notes,
            'merged_at': now
        })

        return self.transition_status(source_id, 'merged')

    # ==================== Edge Management ====================

    def add_dependency(self, from_id: str, to_id: str,
                       notes: str = '') -> Dict[str, Any]:
        """Branch from_id depends on branch to_id completing first."""
        now = datetime.now(timezone.utc).isoformat()
        self.db.add_edge(from_id, to_id, self.EDGE_DEPENDS_ON, {
            'notes': notes,
            'created_at': now
        })
        return {'success': True}

    def add_reference(self, from_id: str, to_id: str,
                      notes: str = '') -> Dict[str, Any]:
        """Soft link between branches (informational, not enforced)."""
        now = datetime.now(timezone.utc).isoformat()
        self.db.add_edge(from_id, to_id, self.EDGE_REFERENCES, {
            'notes': notes,
            'created_at': now
        })
        return {'success': True}

    def add_artifact_flow(self, from_id: str, to_id: str,
                          artifact_id: str, artifact_type: str) -> Dict[str, Any]:
        """
        Record that an artifact flows from one branch to another.
        Phase 5 feature.
        """
        now = datetime.now(timezone.utc).isoformat()
        self.db.add_edge(from_id, to_id, self.EDGE_ARTIFACT_FLOW, {
            'artifact_id': artifact_id,
            'artifact_type': artifact_type,
            'created_at': now
        })
        return {'success': True}

    # ==================== Queries ====================

    def get_branch_tree(self, root_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Return nested tree structure for sidebar rendering.
        Uses parent_id relationships for hierarchy.
        Returns: [{id, name, type, status, children: [...], artifact_count: 0}]
        """
        branches = self.db.list_branches(parent_id=None if root_id else None)

        # If root_id provided, get branches under that root
        if root_id:
            branches = self.db.get_tree(root_id)
        else:
            # Get all root-level branches (no parent)
            branches = [b for b in branches if not b.get('parent_id')]

        # Build children map
        all_branches = self.db.list_branches()
        children_map: Dict[str, List] = {}
        for b in all_branches:
            parent = b.get('parent_id')
            if parent:
                children_map.setdefault(parent, []).append(b)

        def build_node(branch: Dict) -> Dict:
            branch_id = branch['id']
            children = children_map.get(branch_id, [])

            # Count artifacts (Phase 5)
            artifact_count = 0  # TODO: query artifacts table in Phase 5

            return {
                'id': branch_id,
                'name': branch.get('title', 'Untitled'),
                'type': branch.get('type', self.TYPE_CHAT),
                'status': branch.get('status', 'active'),
                'children': [build_node(c) for c in children],
                'artifact_count': artifact_count,
            }

        return [build_node(b) for b in branches]

    def get_branch_graph(self, domain_filter: Optional[str] = None) -> Dict[str, Any]:
        """
        Return nodes + edges for graph visualization (Phase 6).
        Returns: {nodes: [...], edges: [...]}
        """
        branches = self.db.list_branches()

        if domain_filter:
            # Filter to branches under a specific domain
            branches = [b for b in branches
                        if b['id'] == domain_filter or b.get('root_id') == domain_filter]

        all_edges = []
        for b in branches:
            edges = self.db.get_edges(b['id'], direction='both')
            all_edges.extend(edges)

        return {
            'nodes': branches,
            'edges': all_edges
        }

    def get_branch_with_edges(self, branch_id: str) -> Dict[str, Any]:
        """Get a branch with all its edges."""
        branch = self.db.get_branch(branch_id)
        if not branch:
            return {}

        edges = self.db.get_edges(branch_id, direction='both')
        branch['edges'] = edges
        return branch

    def search_branches(self, query: str = '', status: Optional[str] = None,
                        branch_type: Optional[str] = None,
                        limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Search branches with filters."""
        return self.db.search_branches(
            query=query, status=status, branch_type=branch_type,
            limit=limit, offset=offset
        )

    # ==================== Utilities ====================

    def _generate_id(self) -> str:
        """Generate a unique branch ID."""
        import uuid
        return str(uuid.uuid4())

    def get_valid_transitions(self, current_status: str) -> List[str]:
        """Get allowed status transitions from current status."""
        return list(self.VALID_TRANSITIONS.get(current_status, []))
