"""LLM-powered memory compressor for Gradient Cascade Memory."""

from typing import Any

from core.logging_config import get_logger

logger = get_logger("core", "LLMCompressor")


class LLMCompressor:
    """Compresses memory entries using LLM to create summaries."""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def compress(self, value: Any) -> str:
        """Compress a value into a short summary using LLM."""
        if value is None:
            return ""

        text = str(value)
        if len(text) < 100:
            return text

        prompt = (
            "Summarize the following text in 2-3 sentences, "
            "preserving key facts and context:\n\n"
            f"{text[:2000]}"
        )

        try:
            response = await self.llm.generate(prompt)
            summary = response.get("response", "").strip()
            if summary:
                logger.info("Memory compressed", original_len=len(text), summary_len=len(summary))
                return summary
            return text[:300]
        except Exception as e:
            logger.error("Compression failed", error=str(e))
            return text[:300]
