"""LLM-powered memory compressor - creates summaries instead of truncation."""

import asyncio
from collections.abc import Callable
from typing import Any

from core.logging_config import get_logger

logger = get_logger("core", "LLMCompressor")


class LLMCompressor:
    """Compresses memory entries using LLM to create summaries."""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def compress(self, value: Any) -> str:
        """
        Compress value into a summary using LLM.

        Args:
            value: The value to compress (string, dict, list)

        Returns:
            Compressed summary string
        """
        # Convert value to text
        if isinstance(value, str):
            text = value
        elif isinstance(value, dict):
            text = f"Dictionary with keys: {list(value.keys())}. Content: {value}"
        elif isinstance(value, list):
            text = f"List with {len(value)} items: {value}"
        else:
            text = str(value)

        # If text is already short, return as-is
        if len(text) <= 200:
            return text

        # Use LLM to create summary
        prompt = (
            "Summarize the following content in 2-3 sentences. "
            "Focus on key facts and main points. Be concise.\n\n"
            f"Content:\n{text}\n\n"
            "Summary:"
        )

        try:
            response = await self.llm.generate(prompt)
            summary = response.get("response", "").strip()

            if summary:
                logger.info(
                    "LLM compression successful",
                    original_length=len(text),
                    summary_length=len(summary),
                )
                return summary
            else:
                logger.warning("LLM returned empty summary, using fallback")
                return self._fallback_compress(text)

        except Exception as e:
            logger.error("LLM compression failed", error=str(e))
            return self._fallback_compress(text)

    def _fallback_compress(self, text: str) -> str:
        """Fallback: simple truncation if LLM fails."""
        return text[:200] + "..." if len(text) > 200 else text


def create_compressor(llm_client) -> Callable[[Any], str]:
    """
    Create a synchronous wrapper for LLM compressor.
    Used by MemoryManager.start_background_compression().
    """
    compressor = LLMCompressor(llm_client)

    def sync_compressor(value: Any) -> str:
        """Synchronous wrapper that runs async compression."""
        try:
            # Run async compression in new event loop
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(compressor.compress(value))
            finally:
                loop.close()
        except Exception as e:
            logger.error("Sync compression wrapper failed", error=str(e))
            text = str(value)
            return text[:200] + "..." if len(text) > 200 else text

    return sync_compressor
