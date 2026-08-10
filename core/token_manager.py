"""Token manager - tracks and controls context window usage (T3-018)."""

from typing import Any

from core.logging_config import get_logger
from core.react_types import TokenUsage

logger = get_logger("core", "TokenManager")

CHARS_PER_TOKEN_LATIN = 4.0
CHARS_PER_TOKEN_CYRILLIC = 2.5


class TokenManager:
    """Tracks token usage and trims messages without breaking protocol pairs."""

    def __init__(self, max_tokens: int, safety_margin: int = 1000):
        self.max_tokens = max_tokens
        self.safety_margin = safety_margin

    def estimate_tokens(self, text: str) -> int:
        """Estimate tokens with script-aware coefficients (Cyrillic/Latin)."""
        cyrillic = 0
        for ch in text:
            if "а" <= ch <= "я" or "А" <= ch <= "Я" or ch in "ёЁ":
                cyrillic += 1
        latin = len(text) - cyrillic
        return int(cyrillic / CHARS_PER_TOKEN_CYRILLIC + latin / CHARS_PER_TOKEN_LATIN) + 1

    def count_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Count total tokens in message list."""
        total = 0
        for msg in messages:
            total += self.estimate_tokens(str(msg.get("content", "")))
            total += 4
        return total

    def trim_messages(
        self, messages: list[dict[str, Any]], reserve_tokens: int = 0
    ) -> list[dict[str, Any]]:
        """
        Trim messages to fit the context window.

        Messages are grouped into turns (a new group starts at each user
        message). assistant(tool_calls) and tool messages stay inside their
        group, so trimming never breaks the Ollama protocol pair.
        The first group (original request) and the last group (most recent)
        are always kept; middle groups are dropped oldest-first.
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

        while len(groups) > 2 and total(groups) > available:
            groups.pop(1)

        # Last resort: truncate long tool payloads
        while total(groups) > available:
            longest = None
            for group in groups:
                for msg in group:
                    if msg.get("role") == "tool" and (
                        longest is None
                        or len(msg.get("content", "")) > len(longest.get("content", ""))
                    ):
                        longest = msg
            if longest is None or len(longest.get("content", "")) <= 200:
                break
            longest["content"] = longest["content"][:200] + "..."

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
