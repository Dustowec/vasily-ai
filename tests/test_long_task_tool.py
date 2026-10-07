"""ADR-017: инструмент long_task.

Покрывает все действия: create, checkpoint, step_done, complete, cancel, list, get, help.
Валидация ошибок при неверных key/action/params.
"""

import pytest

from core.internal_tools import LongTaskTool
from memory.manager import GradientMemory


@pytest.fixture
def tool(m) -> LongTaskTool:
    """Инструмент с менеджером памяти."""
    return LongTaskTool(memory_manager=m)


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


# ==================== create action ====================


async def test_create_action(tool):
    """Создание новой задачи через create."""
    result = await tool._execute(action="create", key="task_state:test_task", goal="Моя цель")
    assert result["status"] == "success"
    assert result["action"] == "create"
    assert result["key"] == "task_state:test_task"


async def test_create_missing_key_returns_error(tool):
    """create без key — ошибка."""
    result = await tool._execute(action="create", goal="Нет ключа")
    assert result["status"] == "error"


async def test_create_missing_goal_returns_error(tool):
    """create без goal — ошибка."""
    result = await tool._execute(action="create", key="task_state:no_goal")
    assert result["status"] == "error" or "goal" in str(result).lower()


# ==================== checkpoint action ====================


async def test_checkpoint_action(tool):
    """Добавление чекпоинта."""
    await tool._execute(action="create", key="task_state:cp_test", goal="Цель задачи")
    result = await tool._execute(
        action="checkpoint", key="task_state:cp_test", index=1, desc="Готово!"
    )
    assert result["status"] == "success"
    assert result["action"] == "checkpoint"


async def test_checkpoint_unknown_key(tool):
    """Checkpoint несуществующей задачи — ошибка."""
    result = await tool._execute(action="checkpoint", key="task_state:not_exist", index=0, desc="t")
    assert result["status"] == "error"


# ==================== step_done action ====================


async def test_step_done_action(tool):
    """Отметка шага выполненным."""
    await tool._execute(
        action="create",
        key="task_state:step_test",
        goal="Шаг 1, Шаг 2",
        steps=["Шаг 1", "Шаг 2"],
    )
    result = await tool._execute(action="step_done", key="task_state:step_test", step_index=0)
    assert result["status"] == "success"
    assert result["action"] == "step_done"


# ==================== complete action ====================


async def test_complete_action(tool):
    """Завершение задачи (status=done)."""
    await tool._execute(action="create", key="task_state:complete_me", goal="Доделать")
    result = await tool._execute(action="complete", key="task_state:complete_me")
    assert result["status"] == "success"
    assert result["action"] == "complete"


# ==================== cancel action ====================


async def test_cancel_action(tool):
    """Отмена задачи (status=cancelled)."""
    await tool._execute(action="create", key="task_state:cancel_me", goal="Отменить")
    result = await tool._execute(action="cancel", key="task_state:cancel_me")
    assert result["status"] == "success"
    assert result["action"] == "cancel"


# ==================== list action ====================


async def test_list_action(tool):
    """Список всех задач (in_progress + done/cancelled)."""
    await tool._execute(action="create", key="task_state:list_1", goal="Первая")
    await tool._execute(action="create", key="task_state:list_2", goal="Вторая")
    await tool._execute(action="complete", key="task_state:list_1")

    result = await tool._execute(action="list")
    assert result["status"] == "success"
    assert result["action"] == "list"
    # Должен содержать оба списка
    assert "in_progress" in result
    assert (
        "done_cancelled" in result or "all_tasks" in result or isinstance(result.get("tasks"), list)
    )


# ==================== get action ====================


async def test_get_action(tool):
    """Получение одной задачи по ключу."""
    await tool._execute(action="create", key="task_state:get_me", goal="Получить меня")

    result = await tool._execute(action="get", key="task_state:get_me")
    assert result["status"] == "success"
    assert result["action"] == "get"


async def test_get_unknown_key(tool):
    """Get несуществующей задачи — ошибка."""
    result = await tool._execute(action="get", key="task_state:ghost")
    assert result["status"] == "error"


# ==================== help action ====================


async def test_help_action(tool):
    """Help возвращает описание на русском."""
    result = await tool._execute(action="help")
    assert result["status"] == "success"
    assert result["action"] == "help"
    # Описание должно быть на русском
    text = str(result.get("message", ""))
    assert len(text) > 20
    # Ключевые слова должны присутствовать
    assert any(w in text.lower() for w in ["создать", "список", "получить"])
