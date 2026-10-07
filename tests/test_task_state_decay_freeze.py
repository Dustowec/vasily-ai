"""ADR-017: термодинамический freeze активных задач.

Покрывает: active задача не остывает N тиков, не попадает в distill,
после complete/done начинает остывать по стандартной термодинамике.
"""

import pytest

from memory.manager import GradientMemory


def make_entry(
    score: float,
    *,
    value="текст",
    status="in_progress",
    no_compress=False,
    created_tick=0,
    category=None,
) -> dict:
    """Entry с минимальным набором."""
    return {
        "value": value,
        "score": score,
        "is_cold": False,
        "no_compress": no_compress,
        "shield": False,
        "summary": None,
        "changed_since_revival": False,
        "last_heat_tick": -1,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "created_tick": created_tick,
        "status": status,
        "category": category,
    }


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


# ==================== freeze активной задачи ====================


async def test_active_task_does_not_decay(m):
    """Активная задача (status=in_progress) не меняет score после N тиков."""
    entry = make_entry(40.0, status="in_progress", created_tick=0)
    m._hot["task_state:active_task"] = entry

    initial_score = 40.0
    for _ in range(20):
        await m.decay()

    # score должен остаться прежним — active task frozen
    assert m._hot["task_state:active_task"]["score"] == initial_score
    assert m._hot["task_state:active_task"]["status"] == "in_progress"


async def test_active_task_not_in_distill_queue(m):
    """Активная задача не попадает в очередь дистилляции."""
    entry = make_entry(3.0, status="in_progress", created_tick=0)
    m._hot["task_state:distill_test"] = entry

    before_distill = len(m._distill_queue)
    await m.decay()

    after_distill = len(m._distill_queue)
    # Не должна попасть в очередь — distill freeze работает
    assert after_distill == before_distill
    assert "task_state:distill_test" not in m._distill_queue


async def test_done_task_becomes_normal_after_complete(m):
    """После complete задача начинает остывать нормально."""
    # Создаём задачу со score=5.0 (в зоне дистилляции) и статусом done
    entry = make_entry(5.0, status="done", created_tick=0, no_compress=True)
    m._hot["task_state:done_task"] = entry

    initial_score = 5.0
    for _ in range(10):
        await m.decay()

    # После complete — score должен измениться (остыть)
    assert m._hot["task_state:done_task"]["score"] != initial_score


async def test_cancelled_task_becomes_normal_after_cancel(m):
    """После cancel задача начинает остывать как обычная."""
    entry = make_entry(5.0, status="cancelled", created_tick=0, no_compress=True)
    m._hot["task_state:cancelled_task"] = entry

    initial_score = 5.0
    for _ in range(10):
        await m.decay()

    # После cancel — score должен измениться
    assert m._hot["task_state:cancelled_task"]["score"] != initial_score


async def test_regular_user_fact_still_decays(m):
    """Проверка: обычные user_fact не заморожены — работают как раньше."""
    entry = make_entry(15.0, category="user_fact", created_tick=0, status=None)
    entry["key"] = "user_fact:test_fresh"
    m._hot["user_fact:test_fresh"] = entry

    initial_score = 15.0
    for _ in range(15):
        await m.decay()

    # Обычный fact должен остыть
    assert m._hot["user_fact:test_fresh"]["score"] < initial_score
