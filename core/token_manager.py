"""Token manager - tracks and controls context window usage."""

from typing import Any

from core.logging_config import get_logger

logger = get_logger("core", "TokenManager")

# Approximate tokens per character (English: ~4 chars/token, Russian: ~2.5 chars/token)
CHARS_PER_TOKEN = 3.0
SAFETY_MARGIN = 1000  # Reserve tokens for system prompt and response


class TokenManager:
    """Tracks token usage and trims messages to fit context window."""

    def __init__(self, max_tokens: int):
        self.max_tokens = max_tokens
        self.used_tokens = 0

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count from text length."""
        return int(len(text) / CHARS_PER_TOKEN)

    def count_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Count total tokens in message list."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += self.estimate_tokens(content)
            # Add overhead for role and structure
            total += 4
        return total

    def trim_messages(
        self, messages: list[dict[str, Any]], reserve_tokens: int = 0
    ) -> list[dict[str, Any]]:
        """
        Trim old messages to fit within context window.
        Keeps system prompt and recent messages, removes middle ones.
        """
        available = self.max_tokens - SAFETY_MARGIN - reserve_tokens
        current_tokens = self.count_messages_tokens(messages)

        if current_tokens <= available:
            return messages

        logger.warning(
            "Context overflow detected",
            current_tokens=current_tokens,
            max_tokens=self.max_tokens,
            available=available,
        )

        # Keep system prompt (index 0) and recent messages
        system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
        other_msgs = messages[1:] if system_msg else messages

        # Remove from the middle (keep recent and old, drop middle)
        while self.count_messages_tokens(messages) > available and len(other_msgs) > 2:
            # Remove oldest non-system message
            other_msgs.pop(0)

            if system_msg:
                messages = [system_msg] + other_msgs
            else:
                messages = other_msgs

        logger.info(
            "Messages trimmed",
            original_count=len(messages),
            new_tokens=self.count_messages_tokens(messages),
        )

        return messages

    def get_usage_report(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Get current token usage report."""
        used = self.count_messages_tokens(messages)
        percentage = (used / self.max_tokens) * 100 if self.max_tokens > 0 else 0

        return {
            "used_tokens": used,
            "max_tokens": self.max_tokens,
            "usage_percent": round(percentage, 1),
            "available_tokens": self.max_tokens - used,
        }
