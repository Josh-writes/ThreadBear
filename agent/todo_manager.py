"""
Todo Manager for ThreadBear Agent Engine

Per-branch todo tracking, injected into agent's system prompt.
Stored in branch metadata JSON (not filesystem like lmagent).
Adapted from lmagent agent_core.py:903-1004.
"""
import json
from datetime import datetime, timezone


class TodoManager:
    """
    Per-branch todo tracking.
    """

    def __init__(self, branch_id: str, branch_manager):
        self.branch_id = branch_id
        self.bm = branch_manager
        self.todos = self._load()
        self._next_id = max((t['id'] for t in self.todos), default=0) + 1

    def _load(self) -> list:
        branch = self.bm.db.get_branch(self.branch_id)
        if not branch:
            return []
        meta = json.loads(branch.get('metadata', '{}'))
        return meta.get('todos', [])

    def _save(self):
        branch = self.bm.db.get_branch(self.branch_id)
        if not branch:
            return
        meta = json.loads(branch.get('metadata', '{}'))
        meta['todos'] = self.todos
        self.bm.db.upsert_branch(self.branch_id, metadata=meta)

    def add(self, description: str, notes: str = '') -> dict:
        """Add a new todo item. Returns the created todo dict."""
        todo = {
            'id': self._next_id,
            'description': description,
            'status': 'pending',
            'notes': notes,
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        self._next_id += 1
        self.todos.append(todo)
        self._save()
        return todo

    def update_status(self, todo_id: int, new_status: str, notes: str = None) -> dict:
        """Update todo status. Valid: pending, in_progress, completed, blocked."""
        for t in self.todos:
            if t['id'] == todo_id:
                t['status'] = new_status
                if notes:
                    t['notes'] = notes
                t['updated_at'] = datetime.now(timezone.utc).isoformat()
                self._save()
                return t
        return None

    def complete(self, todo_id: int) -> dict:
        """Shorthand for marking a todo as completed."""
        return self.update_status(todo_id, 'completed')

    def list_all(self) -> dict:
        """Return all todos with status counts."""
        counts = {'pending': 0, 'in_progress': 0, 'completed': 0, 'blocked': 0}
        for t in self.todos:
            status = t.get('status', 'pending')
            counts[status] = counts.get(status, 0) + 1
        return {'todos': self.todos, 'counts': counts}

    def get_context(self) -> str:
        """
        Returns formatted string for system prompt injection.
        Shows top 5 pending/in_progress items.
        """
        if not self.todos:
            return ''

        active = [t for t in self.todos if t['status'] in ('pending', 'in_progress')]
        if not active:
            completed = len([t for t in self.todos if t['status'] == 'completed'])
            if completed > 0:
                return f'YOUR TODO LIST: All {completed} items completed.'
            return ''

        lines = ['YOUR TODO LIST:']
        for t in active[:5]:
            icon = {
                'pending': '[  ]',
                'in_progress': '[>>]',
                'completed': '[OK]',
                'blocked': '[!!]'
            }.get(t['status'], '[??]')
            lines.append(f"  {icon} #{t['id']}: {t['description']}")
            if t.get('notes'):
                lines.append(f"       Note: {t['notes']}")

        remaining = len(active) - 5
        if remaining > 0:
            lines.append(f"  ... and {remaining} more items")

        return '\n'.join(lines)
