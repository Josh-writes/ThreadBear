"""
Loop Detector for ThreadBear Agent Engine

Detects when an agent is stuck in a loop.
Adapted from lmagent agent_core.py:1842-1893.
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class LoopDetector:
    """
    Detects when an agent is stuck in a loop.
    
    Triggers on:
    - Repeated identical tool calls (same name + args hash)
    - Consecutive errors (error_streak >= max_errors)
    - Empty iterations (no tools and no meaningful content)
    """
    max_repeats: int = 3
    max_errors: int = 3
    max_empty: int = 5

    tool_history: List[Tuple[str, str]] = field(default_factory=list)  # (name, args_hash)
    error_streak: int = 0
    empty_streak: int = 0
    _reason: Optional[str] = None

    def record_tool_call(self, name: str, args: dict, result: dict):
        """Record a tool call for loop tracking."""
        args_hash = hashlib.md5(
            json.dumps(args, sort_keys=True).encode()
        ).hexdigest()[:8]
        self.tool_history.append((name, args_hash))

        # Keep only last 30 entries
        if len(self.tool_history) > 30:
            self.tool_history = self.tool_history[-30:]

        if not result.get('success'):
            self.error_streak += 1
        else:
            self.error_streak = 0

        self.empty_streak = 0  # Tool call means not empty

    def record_empty_iteration(self):
        """Record an iteration with no tools and no meaningful content."""
        self.empty_streak += 1

    def is_looping(self) -> bool:
        """Check if the agent appears stuck."""
        # Check repeated identical tool calls
        if len(self.tool_history) >= self.max_repeats:
            recent = self.tool_history[-self.max_repeats:]
            if len(set(recent)) == 1:
                self._reason = f"Repeated {recent[0][0]} call {self.max_repeats} times with same args"
                return True

        # Check error streak
        if self.error_streak >= self.max_errors:
            self._reason = f"{self.error_streak} consecutive tool errors"
            return True

        # Check empty iterations
        if self.empty_streak >= self.max_empty:
            self._reason = f"{self.empty_streak} iterations with no progress"
            return True

        return False

    def get_reason(self) -> str:
        return self._reason or 'Unknown loop condition'

    def reset(self):
        """Reset all counters."""
        self.tool_history.clear()
        self.error_streak = 0
        self.empty_streak = 0
        self._reason = None

    def to_dict(self) -> dict:
        """Serialize for persistence in branch metadata."""
        return {
            'tool_history': self.tool_history,
            'error_streak': self.error_streak,
            'empty_streak': self.empty_streak,
            'max_repeats': self.max_repeats,
            'max_errors': self.max_errors,
            'max_empty': self.max_empty
        }

    @classmethod
    def from_dict(cls, d: dict, **kwargs) -> 'LoopDetector':
        """Restore from persisted state."""
        ld = cls(
            max_repeats=d.get('max_repeats', 3),
            max_errors=d.get('max_errors', 3),
            max_empty=d.get('max_empty', 5),
            **kwargs
        )
        ld.tool_history = [tuple(t) for t in d.get('tool_history', [])]
        ld.error_streak = d.get('error_streak', 0)
        ld.empty_streak = d.get('empty_streak', 0)
        return ld
