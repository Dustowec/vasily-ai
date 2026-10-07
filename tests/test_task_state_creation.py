"""ADR-017: создание задач через task_state канал.

Покрывает: ключ с префиксом task_state:, score=40, no_compress=True,
status=in_progress, поля goal/steps/checkpoints на месте.
"""

import pytest

from memory.manager import GradientMemory


def make_entry(
    score: float,
    *,
    value="текст",
    summary=None,
    no_compress=False,
    updated_at="2026-01-01T00:00:00",
    created_tick=0,
    status=None,
) -> dict:
    """Белый ящик: минимальный корректный entry."""
    return {
        "value": value,
        "score": score,
        "is_cold": False,
        "no_compress": no_compress,
        "shield": False,
        "summary": summary,
        "changed_since_revival": False,
        "last_heat_tick": -1,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": updated_at,
        "created_tick": created_tick,
        "status": status,
    }


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


# ==================== создание задачи ====================


async def test_create_task_key_prefix(m):
    """Ключ начинается с task_state:."""
    result = await m.remember_task_state("task_state:parser_logs", "Написать парсер")
    assert result["stored"] is True
    assert result["key"].startswith("task_state:")


async def test_create_task_score_40(m):
    """Score при создании равен 40."""
    await m.remember_task_state("task_state:my_task", "Цель задачи")
    # Задача должна быть в HOT зоне
    key = "task_state:my_task"
    assert key in m._hot
    assert m._hot[key]["score"] == 40


async def test_create_task_no_compress_true(m):
    """no_compress=True при создании."""
    await m.remember_task_state("task_state:compress_test", "Не сжимать")
    key = "task_state:compress_test"
    assert key in m._hot
    assert m._hot[key]["no_compress"] is True


async def test_create_task_status_in_progress(m):
    """Статус по умолчанию in_progress."""
    await m.remember_task_state("task_state:action_test", "Действие")
    key = "task_state:action_test"
    assert key in m._hot
    assert m._hot[key]["status"] == "in_progress"


async def test_create_task_has_goal_field(m):
    """Поле goal присутствует в entry."""
    await m.remember_task_state("task_state:goal_test", "Моя цель здесь")
    key = "task_state:goal_test"
    assert key in m._hot
    assert m._hot[key].get("goal") == "Моя цель здесь"


async def test_create_task_has_steps_list(m):
    """Поля steps и checkpoints присутствуют (пустые по умолчанию)."""
    await m.remember_task_state("task_state:steps_test", "Цель со шагами", steps=["с1", "с2"])
    key = "task_state:steps_test"
    assert key in m._hot
    assert m._hot[key]["steps"] == ["с1", "с2"]
    assert isinstance(m._hot[key]["checkpoints"], list)
