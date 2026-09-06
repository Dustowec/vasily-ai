"""LLM-powered memory compressor for Gradient Cascade Memory.
Stage 5c: known_facts parameter (ADR-012 §3.5 — summary dedup protection),
clean dict-to-text extraction instead of str(dict) for the LLM prompt.
"""

from typing import Any

from core.logging_config import get_logger
from integrations.ollama_client import OllamaClient

logger = get_logger("core", "LLMCompressor")


class LLMCompressor:
    """Compresses memory entries using LLM to create summaries."""

    def __init__(self, llm_client: OllamaClient):
        self.llm = llm_client

    @staticmethod
    def _value_to_text(value: Any) -> str:
        """Extract human-readable text from stored memory values.

        Values can be str, dict (dialogue entries with summary or
        user/assistant keys) or anything else. Python repr of dicts is
        noisy for the LLM, so known shapes are formatted explicitly.
        """
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            if "summary" in value and isinstance(value["summary"], str):
                base = value["summary"]
                raw_preview = value.get("raw_preview")
                if isinstance(raw_preview, str) and raw_preview:
                    return f"{base}\nФрагмент диалога: {raw_preview}"
                return base
            if "user" in value and "assistant" in value:
                user = str(value.get("user", ""))[:500]
                assistant = str(value.get("assistant", ""))[:500]
                return f"Пользователь: {user}\nАссистент: {assistant}"
        return str(value)

    async def compress(self, value: Any, known_facts: list[str] | None = None) -> str:
        """Compress a value into a short summary using LLM.

        known_facts: facts already stored in memory. They are passed to the
        LLM with an instruction NOT to repeat them in the summary
        (ADR-012 §3.5 — prevents summaries from duplicating user facts).
        Backward compatible: compress_cycle calls compressor(value).
        """
        if value is None:
            return ""

        text = self._value_to_text(value)
        if len(text) < 100:
            return text

        known_block = ""
        if known_facts:
            facts = [str(f)[:200] for f in known_facts if f][:5]
            if facts:
                listed = "\n".join(f"- {f}" for f in facts)
                known_block = "УЖЕ ИЗВЕСТНЫЕ ФАКТЫ (НЕ ПОВТОРЯЙ ИХ В РЕЗЮМЕ):\n" f"{listed}\n\n"

        # Явный запрет на размышления в промпте
        prompt = (
            "Сделай краткое резюме следующего текста в 2-3 предложениях, "
            "сохранив ключевые факты и контекст. "
            "ВАЖНО: Верни ТОЛЬКО итоговый текст резюме. НЕ используй теги <thinking>, "
            "НЕ пиши ход своих размышлений. Начинай ответ сразу с сути.\n\n"
            f"{known_block}"
            f"Текст для сжатия:\n{text[:2000]}"
        )
        try:
            response = await self.llm.generate(prompt)
            raw_summary = response.get("response", "").strip()

            # Дополнительная защита: вырезаем <thinking>, если модель всё же его добавила
            _, summary = OllamaClient.extract_thinking_and_answer(raw_summary)

            if summary:
                logger.info(
                    "Memory compressed",
                    original_len=len(text),
                    summary_len=len(summary),
                    known_facts_count=len(known_facts or []),
                )
                return summary

            return text[:300]
        except Exception as e:
            logger.error("Compression failed", error=str(e))
            return text[:300]
