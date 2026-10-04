"""Тесты токенизации и стемминга (Шаг 2 Задачи №2, ADR-013 §9).

Закрывают два класса проблем substring-поиска:
  1. Ложное срабатывание: "кот" in "который" → True (должно стать False).
  2. Пропуск словоформ: "кот" не находит "коты" (должно стать True).

На старой логике `_find_candidates` (substring через `any(w in text)`)
тесты 1 и 2 красные.
"""

import pytest

from memory.manager import GradientMemory
from memory.tokenizer import matches, tokenize


# ==================== Юнит: tokenize / matches ====================


def test_word_boundary_blocks_false_match():
    """«кот» НЕ должен находиться в «который».

    На старой логике (`"кот" in "который"`) это True → тест красный.
    """
    query = tokenize("кот")
    text = tokenize("он который раз это делает")
    assert not matches(query, text), f"Ложное срабатывание: query={query}, text={text}"


def test_stemming_matches_word_forms():
    """«коты» должны находить «кот спит» — стеммер приводит к одной основе."""
    query = tokenize("коты")
    text = tokenize("кот спит на подоконнике")
    assert matches(query, text), f"Стемминг не сработал: {query} vs {text}"


def test_exact_word_still_found():
    """Базовый случай: точное слово находится."""
    query = tokenize("кот")
    text = tokenize("кот спит")
    assert matches(query, text)


def test_lowercase_before_stemming():
    """«Котофей» (заглавная) и «котофей» должны дать одну основу.

    Стеммер сохраняет регистр («Котофей» → «Котоф», «котофей» → «котоф»),
    поэтому .lower() должен применяться ДО стемминга.
    """
    assert tokenize("Котофей") == tokenize("котофей")


def test_empty_text_gives_empty_set():
    """Пустой вход — пустое множество, без исключений."""
    assert tokenize("") == set()
    assert tokenize(None) == set()


# ==================== Интеграция: recall_memory ====================


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер: все файлы памяти — во временной папке."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


async def test_recall_does_not_find_substring_junk(m):
    """Интеграционный: retrieval больше не путает «кота» с «который».

    Запрос «кот», в памяти есть кот (релевантно) и мусор со словом
    «который» (ложное substring-совпадение). После Шага 2 retrieval
    возвращает только кота — мусор отсеивается на уровне токенизации,
    а не полагается на LLM-ранк (Задача №3).

    На старой логике retrieval возвращал обоих — тест красный.
    """
    await m.remember_user_fact("user_fact:cat", "кот спит на подоконнике")
    await m.remember_user_fact("user_fact:junk", "он который раз это делает")

    # блок: сдвиг тика, чтобы отделить создание от recall (см. соседние тесты).
    m._total_ticks += 1

    result = await m.recall_memory("кот")

    keys = {f["key"] for f in result["facts"]}
    assert keys == {
        "user_fact:cat"
    }, f"Retrieval вернул мусор: {sorted(keys)}. Ожидался только user_fact:cat."
