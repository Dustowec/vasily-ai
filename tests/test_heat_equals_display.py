"""Чек-поинт ADR-013 §9: инвариант «нагрев = показ».

Тест-страховка для Шага 1 рефакторинга памяти (разделение recall_memory
на retrieval / rerank / gate) и всех последующих правок.

Инвариант: факт, не попавший в контекст LLM, НЕ должен обновлять
last_heat_tick. Иначе:
  - искажается динамика остывания (анти-дубль-нагрев в тот же тик);
  - scores утекают вверх быстрее, чем реально используются;
  - TGS переполняется «мёртвыми» записями.

На старой логике (до Шага 0) оба теста красные:
  - при 10 совпадениях и limit=3 грелись 5 (то, что менеджер отдал);
  - при пустом результате heat_facts вызывался с пустым списком, но
    в будущих рефакторингах легко наступить на «нагреть всех кандидатов».
"""

import pytest

from core.internal_tools import RecallMemoryTool
from memory.manager import GradientMemory


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер: все файлы памяти — во временной папке.

    Полностью повторяет фикстуру из test_revival_lifecycle.py, чтобы
    файл был самодостаточным. При желании можно потом вынести в conftest.
    """
    return GradientMemory(data_dir=str(tmp_path / "data"))


def _all_entries(m: GradientMemory) -> dict:
    """Все живые entries из всех трёх зон: {key: entry}.

    Порядок не гарантирован, ключи уникальны (менеджер сам следит за этим
    в _load_all и при переходах между зонами).
    """
    return {**m._tgs, **m._hot, **m._cold}


def _heated_keys(m: GradientMemory, tick: int) -> set[str]:
    """Ключи, у которых last_heat_tick == tick.

    last_heat_tick обновляется _apply_heat() и _revive_entry(). Равный
    текущему тику означает «нагрет в этом тике».
    """
    return {k for k, e in _all_entries(m).items() if e.get("last_heat_tick") == tick}


# ==================== §9: нагрев = показ ====================


async def test_heat_equals_display_3_of_10(m):
    """10 совпадений, limit=3 -> нагреты ровно 3, и это ровно те,
    что ушли в контекст LLM.

    Проверяет инвариант §9 ADR-013. Ломается на старой логике, где
    heat_facts получал все 5 фактов из manager.recall_memory (facts[:5]),
    а не только limit показанных.
    """
    # блок: записываем 10 user_fact с общим словом "кот".
    # почему: substring-поиск найдёт их все, значит manager вернёт
    # facts[:5] (внутренний лимит), а tool обрежет до limit=3.
    # Разница 5 vs 3 — именно то, что ловит тест.
    for i in range(10):
        await m.remember_user_fact(f"user_fact:{i:02d}", f"факт номер {i} про кота")

    # блок: вручную инкрементим tick.
    # почему: _store_entry ставит last_heat_tick = текущему тику при
    # создании. Если сразу сделать recall в том же тике, _apply_heat
    # подавит нагрев как "тот же тик" — тест станет бессмысленным.
    # В проде этот сдвиг делает memory.decay() в конце каждого запроса.
    m._total_ticks += 1
    recall_tick = m._total_ticks

    # блок: llm_client=None — _expand_query вернёт исходный query без синонимов.
    # почему: тесту нужен только substring-поиск, LLM здесь не участвует.
    tool = RecallMemoryTool(memory_manager=m, llm_client=None)
    result = await tool._execute(query="кот", limit=3)

    # блок: базовая проверка результата.
    # почему: если ничего не нашли — тест не про то, что мы хотим.
    assert result["found"] is True
    assert len(result["facts"]) == 3

    # блок: главная проверка — ровно 3 entry обновили last_heat_tick.
    # почему: на старой логике здесь было бы 5 (все, что менеджер отдал).
    heated = _heated_keys(m, recall_tick)
    assert (
        len(heated) == 3
    ), f"Ожидалось 3 нагретых, нагрето {len(heated)}: {sorted(heated)}"

    # блок: сильнее — это должны быть ИМЕННО те, что показаны LLM.
    # почему: даже 3 нагретых могут быть не теми 3-мя, если порядок
    # нагрев vs обрезка не совпадает. Здесь ловим именно это.
    shown = {f["key"] for f in result["facts"]}
    assert (
        heated == shown
    ), f"Нагрев {sorted(heated)} не совпадает с показом {sorted(shown)}"


async def test_no_heat_when_nothing_found(m):
    """Пустой результат поиска -> НИКТО не нагревается.

    Защищает от «нагреть всех кандидатов на всякий случай» — seductive
    shortcut при будущем рефакторинге retrieval/rerank/gate.
    """
    # блок: seeding 5 фактов про собак.
    # почему: нужны живые entries, чтобы было кого (не) греть.
    for i in range(5):
        await m.remember_user_fact(f"user_fact:{i:02d}", f"запись номер {i} о собаке")

    # блок: сдвиг тика, как в первом тесте.
    m._total_ticks += 1
    recall_tick = m._total_ticks

    # блок: запрос, который не совпадёт ни с одной записью.
    # почему: substring-поиск ищет "уникальное_слово_xyz" — оно есть
    # только тут, значит recall_memory вернёт found=False.
    tool = RecallMemoryTool(memory_manager=m, llm_client=None)
    result = await tool._execute(query="уникальное_слово_xyz", limit=3)

    # блок: сначала убеждаемся, что действительно ничего не нашли.
    # почему: иначе тест проверяет не то, что задумано.
    assert result["found"] is False
    assert result["facts"] == []

    # блок: главная проверка — пустое множество нагретых.
    # почему: «нечего греть» не должно случайно превращаться
    # в «нагреть всех» — это разные вещи.
    heated = _heated_keys(m, recall_tick)
    assert (
        heated == set()
    ), f"Никто не должен был нагреться, но нагреты: {sorted(heated)}"
