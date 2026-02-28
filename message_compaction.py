"""
Message compaction for ThreadBear chat conversations.

When conversation token count exceeds a threshold, older low-priority messages
are summarized to reduce context size. Adapted from lmagent's compact_messages().
"""
from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple


class TokenCounter:
    """Estimate tokens without tiktoken dependency."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough estimate: ~3.5 characters per token."""
        if not text:
            return 0
        return int(len(text) / 3.5)

    @staticmethod
    def count_message_tokens(messages: List[Dict[str, Any]]) -> int:
        """Estimate total tokens across a list of messages.
        Adds ~10 tokens per message for role/formatting overhead.
        """
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, list):
                # Multi-part content (e.g. vision messages)
                for part in content:
                    if isinstance(part, dict):
                        total += TokenCounter.estimate_tokens(part.get("text", ""))
                    elif isinstance(part, str):
                        total += TokenCounter.estimate_tokens(part)
            else:
                total += TokenCounter.estimate_tokens(str(content))
            total += 10  # per-message overhead
        return total


class MessageCompactor:
    """Compacts chat message history when it exceeds token thresholds.

    Pipeline:
    1. Check threshold — return early if under
    2. Split: tail = last keep_recent messages (always kept), candidates = rest
    3. Score candidates by role priority
    4. Mark lowest-scored for removal until under ceiling
    5. Pair tool calls (no-op until Phase 3, but structure included)
    6. Build summary string from removed messages
    7. Insert summary as system message at position 0
    """

    # Default thresholds
    DEFAULT_THRESHOLD = 80000   # tokens before compaction triggers
    DEFAULT_KEEP_RECENT = 30    # always keep last N messages
    DEFAULT_CEILING = 0.75      # compact to 75% of threshold

    def __init__(self, config_manager=None):
        self.config_manager = config_manager
        self.default_threshold = self.DEFAULT_THRESHOLD
        self.keep_recent = self.DEFAULT_KEEP_RECENT
        self.ceiling = self.DEFAULT_CEILING

    def get_threshold(self, provider: Optional[str] = None,
                      model: Optional[str] = None) -> int:
        """Get compaction threshold based on model's context window.

        Uses 80% of context_window from config_manager if available,
        otherwise falls back to default_threshold.
        """
        if self.config_manager and provider and model:
            try:
                context_window = self.config_manager.get_context_window(
                    provider, model
                )
                if context_window and context_window > 0:
                    return int(context_window * 0.8)
            except Exception:
                pass
        return self.default_threshold

    def should_compact(self, messages: List[Dict[str, Any]],
                       provider: Optional[str] = None,
                       model: Optional[str] = None) -> bool:
        """Check if messages exceed the compaction threshold."""
        if len(messages) <= self.keep_recent + 5:
            return False
        threshold = self.get_threshold(provider, model)
        token_count = TokenCounter.count_message_tokens(messages)
        return token_count > threshold

    def compact_messages(self, messages: List[Dict[str, Any]],
                         provider: Optional[str] = None,
                         model: Optional[str] = None,
                         force: bool = False
                         ) -> Tuple[List[Dict[str, Any]], str]:
        """Compact messages by removing low-priority older messages.

        Args:
            messages: List of messages to compact
            provider: LLM provider name (for context window lookup)
            model: Model name (for context window lookup)
            force: If True, bypass should_compact() threshold check

        Returns:
            (compacted_messages, summary_of_removed)
            If no compaction needed, returns (messages, "").
        """
        threshold = self.get_threshold(provider, model)
        token_count = TokenCounter.count_message_tokens(messages)

        # Not over threshold or too few messages (unless forced)
        if not force and (token_count <= threshold or len(messages) <= self.keep_recent + 5):
            return messages, ""

        target_tokens = int(threshold * self.ceiling)

        # Split into candidates and tail (always kept)
        tail_start = max(0, len(messages) - self.keep_recent)
        candidates = messages[:tail_start]
        tail = messages[tail_start:]

        if not candidates:
            return messages, ""

        # Score candidates
        scored = self._score_messages(candidates)

        # Sort by score ascending (lowest scores removed first)
        scored.sort(key=lambda x: (x[1], x[0]))

        # Determine which messages to keep vs remove
        keep_indices: set = set()
        remove_indices: set = set()

        # Start removing lowest-scored until we'd be under target
        tail_tokens = TokenCounter.count_message_tokens(tail)
        remaining_budget = target_tokens - tail_tokens

        # First, figure out how many candidate tokens we can keep
        # Add messages from highest-scored to lowest until budget is full
        scored_desc = list(reversed(scored))
        budget_used = 0
        for idx, score in scored_desc:
            msg = candidates[idx]
            msg_tokens = TokenCounter.estimate_tokens(
                str(msg.get("content", ""))
            ) + 10
            if budget_used + msg_tokens <= remaining_budget:
                keep_indices.add(idx)
                budget_used += msg_tokens
            else:
                remove_indices.add(idx)

        # Mark any remaining unprocessed as removed
        for idx, _ in scored:
            if idx not in keep_indices:
                remove_indices.add(idx)

        # Pair tool calls — keep pairs together
        self._pair_tool_calls(candidates, keep_indices)

        # If nothing to remove, return as-is
        if not remove_indices:
            return messages, ""

        # Build summary from removed messages
        removed = [candidates[i] for i in sorted(remove_indices)
                   if i < len(candidates)]
        summary = self._build_summary(removed)

        # Rebuild message list
        kept_candidates = [candidates[i] for i in sorted(keep_indices)
                           if i < len(candidates)]

        result: List[Dict[str, Any]] = []

        # Insert compaction summary as first message
        result.append({
            "role": "system",
            "content": (
                f"[COMPACTION SUMMARY]\n"
                f"The following summarizes {len(removed)} older messages "
                f"that were compacted to save context space:\n\n"
                f"{summary}\n\n"
                f"IMPORTANT: Preserve all IDs, UUIDs, filenames, and URLs exactly "
                f"as they appear in the conversation history.\n"
                f"[END COMPACTION SUMMARY]"
            )
        })

        # Add kept candidate messages
        result.extend(kept_candidates)

        # Add tail (always kept recent messages)
        result.extend(tail)

        new_tokens = TokenCounter.count_message_tokens(result)
        print(f"[Compaction] {token_count} -> {new_tokens} tokens "
              f"({len(messages)} -> {len(result)} msgs, "
              f"removed {len(removed)} messages)")

        return result, summary

    def _score_messages(self, messages: List[Dict[str, Any]]
                        ) -> List[Tuple[int, int]]:
        """Score messages by priority.

        Scoring:
        - system messages: 900 (almost never removed)
        - user messages: 500
        - assistant messages with substantial content (>50 chars): 300
        - assistant short replies: 100
        - tool_calls / tool responses: 400 (will matter in Phase 3)
        """
        scored: List[Tuple[int, int]] = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            content = str(msg.get("content", ""))

            if role == "system":
                scored.append((i, 900))
            elif role == "user":
                scored.append((i, 500))
            elif role == "assistant":
                if msg.get("tool_calls"):
                    scored.append((i, 400))
                elif len(content) > 50:
                    scored.append((i, 300))
                else:
                    scored.append((i, 100))
            elif role == "tool":
                scored.append((i, 400))
            else:
                scored.append((i, 200))

        return scored

    def _pair_tool_calls(self, messages: List[Dict[str, Any]],
                         keep_indices: set) -> None:
        """Ensure tool_call and tool_response messages are kept/dropped together.

        Fixed-point loop from lmagent — no-op until tools exist in Phase 3,
        but included so compaction works correctly when tools are added.
        """
        while True:
            new_indices: set = set()
            for i in sorted(keep_indices):
                if i >= len(messages):
                    continue
                msg = messages[i]
                if msg.get("role") == "tool":
                    # Find the assistant message that made this tool call
                    tid = msg.get("tool_call_id")
                    if not tid:
                        continue
                    for j in range(i - 1, -1, -1):
                        prev = messages[j]
                        if (prev.get("role") == "assistant"
                                and prev.get("tool_calls")
                                and any(tc.get("id") == tid
                                        for tc in prev["tool_calls"])):
                            if j not in keep_indices:
                                new_indices.add(j)
                            break
                elif (msg.get("role") == "assistant"
                      and msg.get("tool_calls")):
                    # Find the tool response for this call
                    for tc in msg["tool_calls"]:
                        tid = tc.get("id")
                        if not tid:
                            continue
                        for j in range(i + 1, len(messages)):
                            if (messages[j].get("role") == "tool"
                                    and messages[j].get("tool_call_id") == tid):
                                if j not in keep_indices:
                                    new_indices.add(j)
                                break
            if not new_indices:
                break
            keep_indices |= new_indices

    def _build_summary(self, removed: List[Dict[str, Any]]) -> str:
        """Build a text summary of removed messages."""
        if not removed:
            return ""

        lines: List[str] = []
        user_topics: List[str] = []
        assistant_points: List[str] = []

        for msg in removed:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))

            if role == "user":
                # Extract first line or first 100 chars as topic
                first_line = content.split("\n")[0][:100]
                if first_line:
                    user_topics.append(first_line)
            elif role == "assistant":
                # Extract first sentence or first 100 chars
                first_sentence = content.split(".")[0][:100]
                if first_sentence:
                    assistant_points.append(first_sentence)

        if user_topics:
            lines.append("User discussed:")
            for topic in user_topics[:10]:  # Cap at 10
                lines.append(f"  - {topic}")

        if assistant_points:
            lines.append("Assistant covered:")
            for point in assistant_points[:10]:
                lines.append(f"  - {point}")

        lines.append(f"\n({len(removed)} messages total were compacted)")

        return "\n".join(lines)
