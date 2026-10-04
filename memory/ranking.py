"""LLM-ранжирование кандидатов памяти (ADR-013 §9, Задача №3).

Семантический отбор без эмбеддингов: основная LLM получает список
кандидатов (короткие превью) и возвращает JSON с номерами релевантных.

Одна точка правды для двух сценариев:
  - rank_for_recall   — какие факты по теме запроса (перед нагревом);
  - filter_for_forget — какие факты относятся к теме удаления.

Контракт одинаковый: на входе список dict'ов с 'key'/'value'/'summary',
на выходе — подмножество того же списка, в исходном порядке.

Почему не эмбеддинги:
  - дорого на локальном инференсе;
  - непрозрачно (не видно, почему факт выбран);
  - ломает «трассируемые числа» ADR-013 — LLM-ранжирование даёт
    явный лог: какие ids она выбрала.
"""

import asyncio
import json
import re

from core.logging_config import get_logger
from integrations.ollama_client import OllamaClient

logger = get_logger("core", "MemoryRanking")

# блок: таймаут вызова LLM на ранжирование.
# почему: 10 секунд — компромисс между «дать модели шанс» и «не завесить
# recall». При timeout сработает fallback-политика on_error.
DEFAULT_TIMEOUT = 10.0

# блок: превью кандидата в промпте.
# почему: ТЗ Задачи №3 требует 150 символов — этого хватает, чтобы LLM
# поняла, о чём факт, но не раздувает контекст. 30 кандидатов × 150 симв
# ≈ 1500 токенов — ничто для модели.
RECALL_PREVIEW_CHARS = 150


def _format_entry(index: int, entry: dict, max_chars: int | None) -> str:
    """Строка 'N. <текст>' для одной позиции в промпте.

    max_chars=None — без обрезки (forget: полный контекст);
    max_chars=150   — типичный recall-режим.
    """
    text = entry.get("summary") or str(entry.get("value") or "")
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars] + "..."
    return f"{index}. {text}"


def _build_prompt(entries: list[str], intro: str, criteria: str) -> str:
    """Собирает промпт из intro, entries и инструкции про JSON.

    Структура одна для обоих сценариев — меняются intro и criteria.
    """
    lines = [intro]
    lines.extend(entries)
    lines.append(f'Верни ТОЛЬКО JSON-объект: {{"ids": [1, 2]}}. Где ids — {criteria}')
    return "\n".join(lines)


async def _select_by_llm(
    llm_client,
    candidates: list[dict],
    *,
    intro: str,
    criteria: str,
    preview_chars: int | None,
    on_error: str,
    timeout: float,
) -> list[dict]:
    """Общий механизм: промпт → LLM → парсинг ids → подмножество candidates.

    on_error:
      "all"   — при сбое вернуть ВСЕХ (recall: не теряем данные);
      "empty" — при сбое вернуть ПУСТО (forget: ничего не удаляем).
    """
    if not candidates:
        return []

    entries = [_format_entry(i + 1, c, preview_chars) for i, c in enumerate(candidates)]
    prompt = _build_prompt(entries, intro, criteria)

    try:
        # блок: вызов LLM с таймаутом.
        # почему: если модель зависнет, recall не должен ждать вечно.
        response = await asyncio.wait_for(
            llm_client.generate(prompt, temperature=0.0),
            timeout=timeout,
        )

        # блок: извлекаем текст ответа.
        # почему: клиент может вернуть dict или str — нормализуем.
        raw = (
            response.get("response", "")
            if isinstance(response, dict)
            else str(response)
        )

        # блок: срезаем <thinking>...</thinking>.
        # почему: Qwen-подобные модели оборачивают рассуждения в теги;
        # для парсинга JSON они не нужны и мешают regex'у.
        _, clean = OllamaClient.extract_thinking_and_answer(raw)

        # блок: ищем JSON-объект в тексте.
        # почему: LLM может добавить пояснения вокруг JSON — ищем
        # первую фигурную скобку и последнюю, regex non-greedy.
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if not match:
            logger.warning("LLM ranking returned no JSON", preview=clean[:100])
            return candidates if on_error == "all" else []

        data = json.loads(match.group(0))
        ids = data.get("ids", [])

        seen: set[int] = set()
        picked: list[dict] = []
        for i in ids:
            # блок: валидация id — int в диапазоне [1, len(candidates)].
            # почему: LLM может галлюцинировать числа за пределами
            # списка или строки — молча отбрасываем.
            if not isinstance(i, int) or not (0 < i <= len(candidates)):
                continue
            # блок: дедуп по id.
            # почему: LLM иногда повторяет один и тот же номер —
            # не хотим показывать факт дважды.
            if i in seen:
                continue
            seen.add(i)
            picked.append(candidates[i - 1])
        return picked

    except Exception as e:
        # блок: любая ошибка — лог + fallback по политике on_error.
        # почему: ранжирование — вспомогательный шаг; не должно ронять
        # основной поток. Recall получит всех, forget — никого.
        logger.warning("LLM ranking failed", error=str(e))
        return candidates if on_error == "all" else []


async def rank_for_recall(
    llm_client,
    candidates: list[dict],
    *,
    query: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict]:
    """RECALL: оставить только релевантные запросу факты.

    При сбое LLM возвращает ВСЕХ кандидатов — recall не должен терять
    данные из-за недоступности модели. Пользователь лучше увидит
    лишний факт, чем пропустит нужный.
    """
    if not candidates or llm_client is None:
        return candidates

    intro = f"Запрос пользователя: '{query}'. Вот найденные факты из памяти:"
    criteria = "номера фактов, которые СТРОГО релевантны запросу."
    return await _select_by_llm(
        llm_client,
        candidates,
        intro=intro,
        criteria=criteria,
        preview_chars=RECALL_PREVIEW_CHARS,
        on_error="all",
        timeout=timeout,
    )


async def filter_for_forget(
    llm_client,
    topic: str,
    candidates: list[dict],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict]:
    """FORGET: оставить только те факты, что относятся к теме удаления.

    При сбое LLM возвращает [] — это безопаснее: пользователь не
    потеряет данные из-за сетевого сбоя, а команду можно повторить.
    """
    if not candidates or llm_client is None:
        return []

    intro = f"Пользователь хочет забыть тему: '{topic}'. Вот найденные факты:"
    criteria = "номера фактов, которые СТРОГО относятся к теме."
    return await _select_by_llm(
        llm_client,
        candidates,
        intro=intro,
        criteria=criteria,
        preview_chars=None,
        on_error="empty",
        timeout=timeout,
    )
