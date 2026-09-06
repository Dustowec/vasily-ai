"""Token manager - tracks and controls context window usage (T3-018).
Stage 1 fixes:
- tool_calls payloads are now counted (they were invisible before);
- oldest dialogue groups are dropped FIRST (recency fix: previously the
  oldest history was immortal while fresh context was trimmed away);
- oversized user messages get head+tail truncation as the final fallback;
- tool truncation keeps head+tail and reports original length.
Stage 1.1 hotfix: each message can be truncated AT MOST ONCE (tracked by
id()). Without this, re-truncating an already-truncated head+tail payload
does not shrink it further and the loop never terminates.
"""

import json
from typing import Any

from core.logging_config import get_logger
from core.react_types import TokenUsage

logger = get_logger("core", "TokenManager")

CHARS_PER_TOKEN_LATIN = 4.0
CHARS_PER_TOKEN_CYRILLIC = 2.5

# Last-resort truncation parameters
TOOL_TRUNCATE_KEEP_HEAD = 120
TOOL_TRUNCATE_KEEP_TAIL = 60
TOOL_TRUNCATE_MIN_LEN = 200
USER_TRUNCATE_KEEP_HEAD = 300
USER_TRUNCATE_KEEP_TAIL = 150
USER_TRUNCATE_MIN_LEN = 500


class TokenManager:
    """Tracks token usage and trims messages without breaking protocol pairs."""

    def __init__(self, max_tokens: int, safety_margin: int = 1000):
        self.max_tokens = max_tokens
        self.safety_margin = safety_margin

    def estimate_tokens(self, text: str) -> int:
        """Estimate tokens with script-aware coefficients (Cyrillic/Latin)."""
        if not text:
            return 1
        cyrillic = sum(1 for ch in text if "а" <= ch <= "я" or "А" <= ch <= "Я" or ch in "ёЁ")
        latin = len(text) - cyrillic
        return int(cyrillic / CHARS_PER_TOKEN_CYRILLIC + latin / CHARS_PER_TOKEN_LATIN) + 1

    def count_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Count total tokens in message list (including tool_calls payloads)."""
        total = 0
        for msg in messages:
            total += self.estimate_tokens(str(msg.get("content") or ""))
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                total += self.estimate_tokens(str(fn.get("name", "")))
                total += self.estimate_tokens(
                    json.dumps(fn.get("arguments") or {}, ensure_ascii=False, default=str)
                )
                total += 4
            total += 4
        return total

    def trim_messages(
        self, messages: list[dict[str, Any]], reserve_tokens: int = 0
    ) -> list[dict[str, Any]]:
        """Trim messages to fit the context window.

        Messages are grouped into turns (a new group starts at each user
        message). assistant(tool_calls) and tool messages stay inside their
        group, so trimming never breaks the Ollama protocol pair.
        The system prompt and the LAST group (current request with its tool
        activity) are always kept; older groups are dropped OLDEST-FIRST.
        If the window still does not fit, long tool payloads and oversized
        user messages are truncated (head + tail). Each message is truncated
        at most once, so the loops always terminate.
        """
        available = self.max_tokens - self.safety_margin - reserve_tokens
        if self.count_messages_tokens(messages) <= available:
            return messages

        logger.warning(
            "Context overflow detected",
            current_tokens=self.count_messages_tokens(messages),
            available=available,
        )

        if messages and messages[0].get("role") == "system":
            system = messages[:1]
            body = list(messages[1:])
        else:
            system = []
            body = list(messages)

        groups: list[list[dict[str, Any]]] = []
        for msg in body:
            if msg.get("role") == "user" or not groups:
                groups.append([msg])
            else:
                groups[-1].append(msg)

        def total(groups_: list[list[dict[str, Any]]]) -> int:
            flat = system + [m for g in groups_ for m in g]
            return self.count_messages_tokens(flat)

        # Drop older groups first; the last group (current request) survives.
        while len(groups) > 1 and total(groups) > available:
            groups.pop(0)

        # Last resort 1: truncate the longest tool payloads (head + tail).
        # cut_tool_ids guarantees each payload is cut at most once —
        # otherwise re-cutting a head+tail payload no longer shrinks it
        # and the loop hangs (regression caught by test_token_trim).
        cut_tool_ids: set[int] = set()
        while total(groups) > available:
            longest = None
            for group in groups:
                for msg in group:
                    if (
                        msg.get("role") == "tool"
                        and id(msg) not in cut_tool_ids
                        and (
                            longest is None
                            or len(msg.get("content", "")) > len(longest.get("content", ""))
                        )
                    ):
                        longest = msg
            if longest is None or len(longest.get("content", "")) <= TOOL_TRUNCATE_MIN_LEN:
                break
            orig_len = len(longest["content"])
            cut_tool_ids.add(id(longest))
            longest["content"] = (
                longest["content"][:TOOL_TRUNCATE_KEEP_HEAD]
                + f"\n...[truncated, was {orig_len} chars]...\n"
                + longest["content"][-TOOL_TRUNCATE_KEEP_TAIL:]
            )

        # Last resort 2: truncate the largest user messages (head + tail).
        # Same one-cut-per-message guarantee.
        cut_user_ids: set[int] = set()
        while total(groups) > available:
            big = max(
                (
                    m
                    for g in groups
                    for m in g
                    if m.get("role") == "user" and id(m) not in cut_user_ids
                ),
                key=lambda m: len(m.get("content") or ""),
                default=None,
            )
            if big is None or len(big.get("content") or "") <= USER_TRUNCATE_MIN_LEN:
                logger.error("Context cannot fit even after all truncation strategies")
                break
            c = big["content"]
            cut_user_ids.add(id(big))
            big["content"] = (
                c[:USER_TRUNCATE_KEEP_HEAD]
                + f"\n...[truncated, was {len(c)} chars]...\n"
                + c[-USER_TRUNCATE_KEEP_TAIL:]
            )

        trimmed = system + [m for g in groups for m in g]
        logger.info(
            "Messages trimmed",
            original_count=len(messages),
            new_count=len(trimmed),
            new_tokens=self.count_messages_tokens(trimmed),
        )
        return trimmed

    def get_usage_report(self, messages: list[dict[str, Any]]) -> TokenUsage:
        """Get current token usage report."""
        used = self.count_messages_tokens(messages)
        percentage = (used / self.max_tokens) * 100 if self.max_tokens > 0 else 0
        return TokenUsage(
            used_tokens=used,
            max_tokens=self.max_tokens,
            usage_percent=round(percentage, 1),
            available_tokens=self.max_tokens - used,
        )
