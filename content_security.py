"""
Content security utilities for ThreadBear.

Wraps external content (documents, memory notes) with unique markers
to help the LLM distinguish injected content from user instructions.
Provides head/tail truncation for oversized content.
"""
from __future__ import annotations
import re
import uuid


def wrap_external_content(content: str, source: str) -> str:
    """Wrap external content with unique boundary markers.

    Sanitizes any fake markers that may have been injected inside the content
    to prevent prompt-injection attacks that try to break out of the wrapper.
    """
    marker_id = uuid.uuid4().hex[:16]
    # Strip any injected markers from the content itself
    sanitized = re.sub(
        r'<<<(?:EXTERNAL_CONTENT|END_EXTERNAL_CONTENT)[^>]*>>>',
        '[[MARKER_SANITIZED]]',
        content,
    )
    return (
        f'<<<EXTERNAL_CONTENT id="{marker_id}">>>\n'
        f'Source: {source}\n'
        f'---\n'
        f'{sanitized}\n'
        f'<<<END_EXTERNAL_CONTENT id="{marker_id}">>>'
    )


def truncate_head_tail(text: str, max_chars: int,
                       source_name: str = "") -> str:
    """Truncate text keeping head (70%) and tail (20%), with a marker in between.

    Returns the original text unchanged if it fits within max_chars.
    """
    if len(text) <= max_chars:
        return text

    head = int(max_chars * 0.70)
    tail = int(max_chars * 0.20)
    omitted = len(text) - head - tail
    label = f" from {source_name}" if source_name else ""
    marker = f"\n\n[...{omitted:,} chars truncated{label}...]\n\n"
    return text[:head] + marker + text[-tail:]
