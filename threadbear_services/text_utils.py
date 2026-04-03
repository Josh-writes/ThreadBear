"""Text truncation utilities for tool and context payloads."""

from __future__ import annotations


def truncate_text_head_tail(text: str, max_chars: int) -> str:
    """Truncate text while preserving the beginning and end segments."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    head = text[:half]
    tail = text[-half:]

    nl = head.rfind("\n")
    if nl > half // 2:
        head = head[:nl]

    nl = tail.find("\n")
    if nl != -1 and nl < half // 2:
        tail = tail[nl + 1 :]

    omitted = len(text) - len(head) - len(tail)
    total_lines = text.count("\n") + 1
    return f"{head}\n\n[... {omitted} chars omitted, {total_lines} total lines ...]\n\n{tail}"


def truncate_tool_result(result: dict, max_chars: int = 3000) -> dict:
    """Truncate large tool-result text fields recursively while preserving JSON shape."""
    truncated = dict(result)
    for field in ["stdout", "stderr", "content", "data"]:
        if field in truncated and isinstance(truncated[field], str):
            truncated[field] = truncate_text_head_tail(truncated[field], max_chars)

    if "result" in truncated and isinstance(truncated["result"], dict):
        truncated["result"] = truncate_tool_result(truncated["result"], max_chars)

    return truncated
