"""ADR-017: recall_memory с фильтром категорий task_state.

Покрывает: recall_memory(categories=["task_state"]) возвращает только задачи,
отсекая user_fact и dialogue_summary.
"""

import pytest

from memory.manager import GradientMemory


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


async def test_recall_task_state_category(m):
    """recall_memory с categories=['task_state'] возвращает только задачи."""
    await m.remember_user_fact("user_fact:cat_test", "Пользователь любит кофе")
    await m.remember_task_state("task_state:cat_work", "Работать над задачей")

    result = await m.recall_memory(query="задача", categories=["task_state"])

    assert result["found"] is True
    for fact in result.get("facts", []):
        assert "key" in fact
        assert fact["key"].startswith("task_state:")


async def test_dialogue_excluded_from_task_state_category(m):
    """dialogue_summary отсекается при filter по task_state."""
    # Диалоговый саммари — не относится к task_state категории
    from tests.test_task_state_creation import make_entry

    entry = make_entry(25.0, value="Обсуждали парсер логов")
    entry["category"] = "dialogue_summary"
    m._hot["dialogue_summary:test"] = entry

    result = await m.recall_memory(query="парсер", categories=["task_state"])

    for fact in result.get("facts", []):
        key = fact["key"]
        assert not key.startswith("dialogue_summary:")
