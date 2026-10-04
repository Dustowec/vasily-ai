"""Тесты LLM-ранжирования кандидатов памяти (ADR-013 §9, Задача №3).

Проверяют, что RecallMemoryTool использует LLM для фильтрации
кандидатов от retrieval: показаны и нагреты ТОЛЬКО релевантные.

Красный тест (test_llm_filters_irrelevant_candidates) не проходит
до внедрения memory/ranking.py и правки internal_tools.py — это
подтверждает, что тест ловит настоящую регрессию, а не зеленеет рядом.
"""

import pytest

from core.internal_tools import RecallMemoryTool
from memory.manager import GradientMemory


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер: все файлы памяти — во временной папке.

    Полностью повторяет фикстуру из test_heat_equals_display.py,
    чтобы файл был самодостаточным.
    """
    return GradientMemory(data_dir=str(tmp_path / "data"))


def _all_entries(m: GradientMemory) -> dict:
    """Все живые entries из всех трёх зон: {key: entry}."""
    return {**m._tgs, **m._hot, **m._cold}


def _heated_keys(m: GradientMemory, tick: int) -> set[str]:
    """Ключи с last_heat_tick == tick (нагреты в этом тике)."""
    return {k for k, e in _all_entries(m).items() if e.get("last_heat_tick") == tick}


class FakeLLM:
    """Минимальный mock OllamaClient.

    Отвечает по-разному на два типа промптов:
      - expand_query: возвращает expand_response (по умолчанию пусто → fallback);
      - rank/forget: возвращает ids_response.

    Различитель — литерал '"ids"' в промпте: он есть только в промпте
    ранжирования/фильтрации, в промпте expand_query его нет.
    """

    def __init__(self, ids_response: str = '{"ids": [1]}', expand_response: str = ""):
        self.ids_response = ids_response
        self.expand_response = expand_response
        self.calls: list[str] = []

    async def generate(self, prompt: str, **kwargs) -> dict:
        self.calls.append(prompt)
        if '"ids"' in prompt:
            return {"response": self.ids_response}
        return {"response": self.expand_response}


# ==================== КРАСНЫЙ ТЕСТ (не проходит сейчас) ====================


async def test_llm_filters_irrelevant_candidates(m):
    """Запрос 'кот': substring ловит 'который' (ложное срабатывание).

    LLM-ранжирование должно оставить только реально релевантный факт.
    На старой логике (без ранжирования) оба кандидата попадают в ответ
    и оба нагреваются — тест ловит именно это.
    """
    # блок: два факта, оба содержат подстроку 'кот'.
    # почему: "который" содержит "кот" как подпоследовательность,
    # substring-поиск не различает слова — это классический red flag из ТЗ.
    await m.remember_user_fact("user_fact:cat", "кот спит на подоконнике")
    await m.remember_user_fact("user_fact:junk", "он который раз это делает")

    # блок: сдвиг тика, чтобы отделить создание от recall.
    # почему: при создании last_heat_tick = текущему тику. Если не сдвинуть,
    # оба факта уже будут «нагреты в этом тике» — тест не отличит
    # реальный нагрев от побочного эффекта remember.
    m._total_ticks += 1
    recall_tick = m._total_ticks

    # блок: LLM возвращает ids=[1] — оставить только первого кандидата.
    # почему: имитируем «LLM распознала мусор». Кандидаты приходят
    # отсортированными по score; порядок сохраняется стабильно
    # (Python sort stable), значит id=1 — это 'user_fact:cat'.
    fake = FakeLLM(ids_response='{"ids": [1]}')
    tool = RecallMemoryTool(memory_manager=m, llm_client=fake)
    result = await tool._execute(query="кот", limit=3)

    # блок: показан ТОЛЬКО кот — не кот и мусор одновременно.
    assert result["found"] is True
    assert len(result["facts"]) == 1, (
        f"Ожидался 1 показанный факт, показано {len(result['facts'])}: "
        f"{[f['key'] for f in result['facts']]}"
    )
    assert result["facts"][0]["key"] == "user_fact:cat"

    # блок: нагрет ТОЛЬКО кот.
    # почему: инвариант §9 «нагрев = показ». Если LLM отбросила мусор,
    # он не должен оставаться «горячим» — иначе статистика опять врёт.
    heated = _heated_keys(m, recall_tick)
    assert heated == {
        "user_fact:cat"
    }, f"Нагрето: {sorted(heated)}, ожидалось только user_fact:cat"


# ==================== ЗЕЛЁНЫЙ GUARD (защищает от регрессии) ====================


async def test_synonym_found_via_expand_and_rank(m):
    """Запрос 'компьютер'. expand_query добавляет 'ноутбук'.
    LLM-ранжирование оставляет релевантный факт.

    Регрессионный guard: подтверждает работу семантического поиска
    через expand_query + rank (без эмбеддингов). Проходит и до, и после
    внедрения — но если кто-то случайно выпилит expand_query или rank,
    тест покраснеет.
    """
    await m.remember_user_fact("user_fact:laptop", "ноутбук сломался вчера")
    await m.remember_user_fact("user_fact:cat", "кот спит на окне")

    m._total_ticks += 1

    # блок: expand возвращает синонимы про ноутбук.
    # почему: без этого substring-поиск по 'компьютер' не найдёт ничего,
    # ведь в тексте факта — 'ноутбук'. LLM расширяет запрос синонимами.
    fake = FakeLLM(
        ids_response='{"ids": [1]}',
        expand_response="ноутбук компьютер пк",
    )
    tool = RecallMemoryTool(memory_manager=m, llm_client=fake)
    result = await tool._execute(query="компьютер", limit=3)

    assert result["found"] is True
    assert len(result["facts"]) == 1
    assert result["facts"][0]["key"] == "user_fact:laptop"
