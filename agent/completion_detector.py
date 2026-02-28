"""
Completion Detector for ThreadBear Agent Engine

Detects when an agent has completed its task.
Adapted from lmagent agent_tools.py:1270-1282.
"""
import re

# Prefixes that indicate the TASK_COMPLETE token is in code/comments, not a real signal
_SKIP_PREFIXES = (
    '#', '//', '"""', "'''", 'print', 'return', 'echo', 'log', '`',
    '<!--', '-->', '*', '-', '>'
)


def detect_completion(content: str, has_tool_calls: bool) -> tuple:
    """
    Detect if the agent has completed its task.
    
    Returns (is_complete: bool, reason: str).

    Detection methods:
    1. Explicit: content contains TASK_COMPLETE (not in code blocks or comments)
    2. Implicit: substantial content (>50 chars) with no tool calls and not asking a question
    """
    if not content:
        return False, ''

    # Method 1: Explicit TASK_COMPLETE marker
    content_stripped = _strip_code_blocks(content)
    for line in content_stripped.split('\n'):
        line_stripped = line.strip()
        if 'TASK_COMPLETE' in line_stripped.upper():
            # Skip if line starts with code/comment prefix
            if not any(line_stripped.startswith(p) for p in _SKIP_PREFIXES):
                return True, 'explicit_completion'

    # Method 2: Implicit — substantial answer without tool calls
    if not has_tool_calls and len(content.strip()) > 50:
        if not _is_asking_question(content):
            return True, 'answer_without_tools'

    return False, ''


def _strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks to avoid false TASK_COMPLETE triggers."""
    return re.sub(r'```[\s\S]*?```', '', text)


def _is_asking_question(text: str) -> bool:
    """Heuristic: does the text end with a question?"""
    last_line = text.strip().split('\n')[-1].strip()
    return last_line.endswith('?')
