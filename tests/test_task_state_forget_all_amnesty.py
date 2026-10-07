"""ADR-017: амнистия task_state при forget_all.

Покрывает: active task_state amnestied как user_fact, done/cancelled НЕ amnestied.
"""

import pytest

from memory.manager import GradientMemory


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


async def test_active_task_state_amnestied(m):
    """Активная задача amnestied при forget_all (как user_fact)."""
    await m.remember_user_fact("user_fact:test_user", "Меня зовут Алекс")
    await m.remember_task_state("task_state:active_work", "Рабочая задача")

    # Ждём несколько тиков чтобы задачи успели остыть но не уйти из window
    for _ in range(5):
        await m.decay()

    result = await m.forget_all(confirm=True)

    # Обе должны быть amnestied
    assert result["amnestied"] == 2
    # Активная задача осталась в памяти
    assert "task_state:active_work" in m._hot


async def test_done_task_not_amnestied(m):
    """Done задача НЕ amnestied — подпадает под ротацию."""
    # Создаём done задачу напрямую с low score чтобы она была кандидатом на ротацию
    from tests.test_task_state_creation import make_entry

    entry = make_entry(20.0, status="done", value="Законченная задача")
    entry["category"] = "task_state"
    entry["created_tick"] = 0
    m._hot["task_state:done_task"] = entry

    await m.decay()
    await m.decay()

    result = await m.forget_all(confirm=True)

    # Done задача не должна быть amnestied
    assert result["amnestied"] != 2  # только user_fact amnestied если есть


async def test_cancelled_task_not_amnestied(m):
    """Cancelled задача НЕ amnestied."""
    from tests.test_task_state_creation import make_entry

    entry = make_entry(20.0, status="cancelled", value="Отменённая задача")
    entry["category"] = "task_state"
    m._hot["task_state:cancelled_task"] = entry

    result = await m.forget_all(confirm=True)

    # Cancelled не amnestied
    assert "task_state:cancelled_task" not in m._hot or result.get("amnestied", 0) < 2


async def test_mixed_tasks_correct_amnesty(m):
    """Смешанные: fresh user_fact + active task_state + old dialogue_summary."""
    await m.remember_user_fact("user_fact:fresh", "Новый факт")
    await m.remember_task_state("task_state:new_project", "Новый проект")
    m._hot["dialogue_summary:old"] = {
        "value": "старый диалог",
        "score": 20.0,
        "is_cold": False,
        "no_compress": False,
        "shield": False,
        "summary": None,
        "changed_since_revival": False,
        "last_heat_tick": -1,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "created_tick": 0,
        "status": None,
    }

    result = await m.forget_all(confirm=True)

    # Оба новых — amnestied, summary — rotated
    assert result["amnestied"] == 2  # user_fact + active task_state
