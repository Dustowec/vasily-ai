"""Фильтрация каналов памяти по префиксу ключа (HANDOFF §7.1 + §7.3).

recall_memory получает параметр categories: список префиксов ключей
до первого ':', по которым искать. None = все каналы (обратная
совместимость с прежним поведением).

Категоризация — по префиксу ключа, без явного поля в entry: данные
не мигрируют, контракт ключа уже де-факто сложился (user_fact:,
dialogue_summary:, в будущем task_state:).

Неизвестная категория в списке НЕ роняет вызов: пишем warning в лог,
фильтр работает как есть. См. HANDOFF §7.1 и §7.3.
"""

import pytest

from memory.manager import GradientMemory


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер: все файлы памяти — во временной папке."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


def _make_entry(score: float, value, **overrides) -> dict:
    """Минимальный корректный entry для вставки в зону.

    Повторяет make_entry из test_user_fact_insurance.py, чтобы файл
    был самодостаточным.
    """
    base = {
        "value": value,
        "score": score,
        "is_cold": False,
        "no_compress": False,
        "shield": False,
        "summary": None,
        "changed_since_revival": False,
        "last_heat_tick": -1,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "created_tick": 0,
    }
    base.update(overrides)
    return base


# ==================== фильтр по одной категории ====================


async def test_categories_filter_excludes_other_channels(m):
    """categories=['user_fact'] → dialogue_summary не показывается.

    Воспроизводит §7.3 HANDOFF: dialogue_summary тонет в общем поиске.
    После фикса — фильтр по префиксу отсекает всё, кроме user_fact.
    """
    await m.remember_user_fact("user_fact:name", "меня зовут Вася")
    m._hot["dialogue_summary:old"] = _make_entry(
        25.0, {"summary": "Пользователь представился, его зовут Вася"}
    )

    result = await m.recall_memory("зовут Вася", categories=["user_fact"])

    assert result["found"] is True
    keys = [f["key"] for f in result["facts"]]
    assert keys == ["user_fact:name"]
    assert "dialogue_summary:old" not in keys


async def test_categories_none_returns_all_channels(m):
    """categories=None → обратная совместимость, ищем во всех каналах.

    Регрессионный guard: старое поведение не должно сломаться.
    """
    await m.remember_user_fact("user_fact:name", "меня зовут Вася")
    m._hot["dialogue_summary:old"] = _make_entry(
        25.0, {"summary": "Пользователь представился, его зовут Вася"}
    )

    result = await m.recall_memory("зовут Вася")  # без categories

    assert result["found"] is True
    keys = {f["key"] for f in result["facts"]}
    assert "user_fact:name" in keys
    assert "dialogue_summary:old" in keys


# ==================== несколько категорий ====================


async def test_multiple_categories_included(m):
    """categories=['user_fact','task_state'] → оба канала ищутся,
    dialogue_summary отсекается.

    Задел под §7.2 HANDOFF (task_state): дедуп в RememberFactTool
    должен учитывать оба канала, значит и здесь — оба.
    """
    await m.remember_user_fact("user_fact:name", "меня зовут Вася")
    m._hot["task_state:job"] = _make_entry(40.0, "Вася пишет код агента")
    m._hot["dialogue_summary:old"] = _make_entry(25.0, {"summary": "Пользователь Вася"})

    result = await m.recall_memory("Вася", categories=["user_fact", "task_state"])

    keys = {f["key"] for f in result["facts"]}
    assert keys == {"user_fact:name", "task_state:job"}
    assert "dialogue_summary:old" not in keys


async def test_empty_category_returns_nothing(m):
    """categories=['task_state'] при отсутствии записей → found=False.

    Даже если по тексту совпадает user_fact — он отсекается фильтром.
    """
    await m.remember_user_fact("user_fact:name", "меня зовут Вася")

    result = await m.recall_memory("меня зовут", categories=["task_state"])

    assert result["found"] is False
    assert result["facts"] == []


# ==================== неизвестная категория ====================


async def test_unknown_category_logs_warning_and_continues(m, monkeypatch):
    """Неизвестная категория — warning в лог, вызов не падает.

    Смысл: опечатка не должна молча давать пустой результат и не
    должна ронять агента. Фильтр работает как есть, но в логе видно.

    Проверяем через monkeypatch на memory.manager.logger.warning,
    потому что structlog-конфигурация проекта может не идти в
    стандартный caplog.
    """
    import memory.manager as mgr_module

    captured: list[tuple[str, dict]] = []
    original_warning = mgr_module.logger.warning

    def spy_warning(msg, **kw):
        captured.append((msg, kw))
        return original_warning(msg, **kw)

    monkeypatch.setattr(mgr_module.logger, "warning", spy_warning)

    await m.remember_user_fact("user_fact:name", "меня зовут Вася")

    result = await m.recall_memory("зовут Вася", categories=["user_fact", "опечатка_xyz"])

    # блок: фильтр всё равно отработал по валидной категории.
    # почему: неизвестная категория не должна «отравить» весь вызов —
    # она просто не даёт совпадений.
    assert result["found"] is True
    assert [f["key"] for f in result["facts"]] == ["user_fact:name"]

    # блок: warning написан и упоминает именно опечатку.
    # почему: без явного упоминания строки диагностика бесполезна.
    joined = " ".join(f"{msg} {kw}" for msg, kw in captured)
    assert "опечатка_xyz" in joined
