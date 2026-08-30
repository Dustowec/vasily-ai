"""LLM-powered memory compressor for Gradient Cascade Memory."""

from typing import Any

from core.logging_config import get_logger
from integrations.ollama_client import OllamaClient

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

        # Явный запрет на размышления в промпте
        prompt = (
            "Сделай краткое резюме следующего текста в 2-3 предложениях, сохранив ключевые факты и контекст. "
            "ВАЖНО: Верни ТОЛЬКО итоговый текст резюме. НЕ используй теги <thinking>, НЕ пиши ход своих размышлений. "
            "Начинай ответ сразу с сути.\n"
            f"Текст для сжатия:\n{text[:2000]}"
        )
        try:
            response = await self.llm.generate(prompt)
            raw_summary = response.get("response", "").strip()

            # Дополнительная защита: вырезаем <thinking>, если модель всё же его добавила
            _, summary = OllamaClient.extract_thinking_and_answer(raw_summary)

            if summary:
                logger.info("Memory compressed", original_len=len(text), summary_len=len(summary))
                return summary

            return text[:300]
        except Exception as e:
            logger.error("Compression failed", error=str(e))
            return text[:300]
