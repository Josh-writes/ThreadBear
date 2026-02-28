"""
Plan Manager for ThreadBear Agent Engine

Step-based plan tracking with dependencies.
Steps must be completed in dependency order.
Injected into agent's system prompt.
Adapted from lmagent agent_core.py:1018-1125.
"""
import json
from datetime import datetime, timezone


class PlanManager:
    """
    Step-based plan tracking with dependencies.
    """

    def __init__(self, branch_id: str, branch_manager):
        self.branch_id = branch_id
        self.bm = branch_manager
        self.plan = self._load()

    def _load(self) -> dict:
        branch = self.bm.db.get_branch(self.branch_id)
        if not branch:
            return None
        meta = json.loads(branch.get('metadata', '{}'))
        return meta.get('plan', None)

    def _save(self):
        branch = self.bm.db.get_branch(self.branch_id)
        if not branch:
            return
        meta = json.loads(branch.get('metadata', '{}'))
        meta['plan'] = self.plan
        self.bm.db.upsert_branch(self.branch_id, metadata=meta)

    def create(self, title: str, steps: list) -> dict:
        """
        Create a plan from list of step dicts.
        Each step: {id, description, dependencies: [step_ids], verification: str}
        """
        self.plan = {
            'title': title,
            'created': datetime.now(timezone.utc).isoformat(),
            'status': 'active',
            'steps': [{
                'id': s.get('id', f'step_{i+1}'),
                'description': s['description'],
                'status': 'pending',
                'dependencies': s.get('dependencies', []),
                'verification': s.get('verification', '')
            } for i, s in enumerate(steps)]
        }
        self._save()
        return self.plan

    def get_next_step(self) -> dict:
        """Return first pending step whose dependencies are all completed."""
        if not self.plan:
            return None
        completed = {s['id'] for s in self.plan['steps'] if s['status'] == 'completed'}
        for step in self.plan['steps']:
            if step['status'] == 'pending':
                deps = set(step.get('dependencies', []))
                if deps.issubset(completed):
                    return step
        return None

    def start_step(self, step_id: str) -> dict:
        """Mark step as in_progress."""
        return self._update_step(step_id, 'in_progress')

    def complete_step(self, step_id: str, notes: str = '') -> dict:
        """Mark step as completed. Validates dependencies are met."""
        if not self.plan:
            return {'error': 'No plan exists'}
        step = next((s for s in self.plan['steps'] if s['id'] == step_id), None)
        if not step:
            return {'error': f'Step {step_id} not found'}
        # Validate dependencies
        completed = {s['id'] for s in self.plan['steps'] if s['status'] == 'completed'}
        deps = set(step.get('dependencies', []))
        if not deps.issubset(completed):
            missing = deps - completed
            return {'error': f'Dependencies not met: {missing}'}
        return self._update_step(step_id, 'completed')

    def _update_step(self, step_id: str, new_status: str) -> dict:
        if not self.plan:
            return None
        for s in self.plan['steps']:
            if s['id'] == step_id:
                s['status'] = new_status
                s['updated_at'] = datetime.now(timezone.utc).isoformat()
                self._save()
                return s
        return None

    def is_complete(self) -> bool:
        """Check if all steps are completed."""
        if not self.plan:
            return True
        return all(s['status'] in ('completed', 'skipped') for s in self.plan['steps'])

    def get_context(self) -> str:
        """
        Returns formatted string for system prompt injection.
        Shows plan title, step statuses, and highlights next step.
        """
        if not self.plan:
            return ''

        lines = [f'YOUR PLAN: {self.plan["title"]}']
        next_step = self.get_next_step()
        for s in self.plan['steps']:
            icon = {
                'pending': '[ ]',
                'in_progress': '[>>]',
                'completed': '[OK]',
                'skipped': '[--]'
            }.get(s['status'], '[??]')
            marker = ' <-- NEXT' if next_step and s['id'] == next_step['id'] else ''
            lines.append(f"  {icon} {s['id']}: {s['description']}{marker}")
        return '\n'.join(lines)
