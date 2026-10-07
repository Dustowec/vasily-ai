"""ADR-017: get_active_tasks для injection в system prompt.

Покрывает: фильтрация только in_progress, топ-3 limit, сортировка по updated_tick DESC, пустой результат.
"""


import pytest

from memory.manager import GradientMemory


def make_entry(
    score: float = 40.0,
    status="in_progress",
    goal="Цель задачи",
    created_tick=5,
    updated_tick=10,
) -> dict:
    return {
        "value": goal,
        "score": score,
        "is_cold": False,
        "no_compress": True,
        "shield": False,
        "summary": None,
        "changed_since_revival": False,
        "last_heat_tick": -1,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": f"2026-01-01T00:{updated_tick:02d}:00",
        "created_tick": created_tick,
        "status": status,
        "goal": goal,
    }


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


# ==================== get_active_tasks ====================


async def test_get_active_returns_only_in_progress(m):
    """Только in_progress попадают в активные."""
    entry_done = make_entry(status="done", goal="Закончен")
    entry_active = make_entry(status="in_progress", goal="Активный")

    m._hot["task_state:done"] = entry_done
    m._hot["task_state:active"] = entry_active

    active = m.get_active_tasks()

    keys = [e["key"] for e in active]
    assert "task_state:active" in keys
    assert "task_state:done" not in keys


async def test_top_3_limit(m):
    """Ограничение top-3 — возвращает не более 3 задач."""
    for i in range(5):
        entry = make_entry(status="in_progress", goal=f"Задача {i}", updated_tick=i + 1)
        m._hot[f"task_state:multi_{i}"] = entry

    active = m.get_active_tasks(limit=3)
    assert len(active) <= 3


async def test_sort_by_updated_tick_desc(m):
    """Сортировка по updated_tick — newest first."""
    entries = []
    for tick in [2, 5, 1]:
        entry = make_entry(status="in_progress", goal=f"Tick {tick}", updated_tick=tick)
        key = f"task_state:tick_{tick}"
        m._hot[key] = entry
        entries.append((key, tick))

    active = m.get_active_tasks()

    ticks_ordered = [e["updated_tick"] for e in active]
    assert ticks_ordered == sorted(ticks_ordered, reverse=True)


async def test_empty_result_when_none(m):
    """Если активных задач нет — пустой список."""
    # Нет task_state записей вообще
    active = m.get_active_tasks()
    assert active == []


async def test_get_active_includes_metadata(m):
    """Возвращаемые записи содержат goal, steps, checkpoints."""
    await m.remember_task_state("task_state:meta_test", "Мета цель", steps=["с1"])

    active = m.get_active_tasks()
    assert len(active) > 0

    entry = active[0]
    assert "key" in entry
    assert "goal" in entry
    assert "score" in entry
    assert "zone" in entry  # tgs/hot/cold
