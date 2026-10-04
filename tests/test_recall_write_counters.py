"""Тесты разделения счётчиков recall/write (Шаг 4 Задачи №2, ADR-013 §4.1).

Проблема: RememberFactTool внутри вызывает recall_memory для дедупа.
Раньше это сбрасывало _ticks_since_recall — и «активный режим
остывания» не включался, хотя пользователь память НЕ читал.

На старой логике (до Шага 4) тесты 1, 2, 3 красные.
"""

import pytest

from core.internal_tools import RememberFactTool
from memory.manager import GradientMemory


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер: файлы памяти во временной папке."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


# ==================== Внешний recall сбрасывает счётчик ====================


async def test_external_recall_resets_counter(m):
    """Публичный recall_memory (external=True) сбрасывает _ticks_since_recall."""
    # блок: искусственно "прокручиваем" время вперёд.
    # почему: в реальной жизни счётчик растёт в decay() после каждого
    # запроса. Здесь имитируем «прошло 5 тиков без чтения».
    m._ticks_since_recall = 5

    await m.recall_memory("что-то")

    assert m._ticks_since_recall == 0


# ==================== Внутренний recall НЕ сбрасывает ====================


async def test_internal_recall_does_not_reset_counter(m):
    """Внутренний recall_memory(external=False) НЕ сбрасывает счётчик.

    Используется RememberFactTool для дедупа — это не пользовательское
    чтение, оно не должно маскировать активный режим.
    """
    m._ticks_since_recall = 5

    await m.recall_memory("что-то", external=False)

    # блок: счётчик остался равен 5.
    # почему: дедуп при записи — не потребление памяти, а служебная
    # проверка. Он не должен «обнулять» простой.
    assert m._ticks_since_recall == 5


# ==================== RememberFactTool использует internal recall ====================


async def test_remember_fact_tool_does_not_reset_recall_counter(m):
    """RememberFactTool._execute не должен сбрасывать _ticks_since_recall.

    На старой логике (до Шага 4) tool вызывал recall_memory() без
    параметра → счётчик сбрасывался → активный режим маскировался.
    """
    m._ticks_since_recall = 7

    tool = RememberFactTool(memory_manager=m, llm_client=None)
    await tool._execute(fact="меня зовут Вася")

    # блок: счётчик не сбросился — дедуп был внутренним.
    assert m._ticks_since_recall == 7


# ==================== Счётчик write ====================


async def test_write_resets_write_counter(m):
    """Успешная запись сбрасывает _ticks_since_write."""
    m._ticks_since_write = 5

    await m.remember_user_fact("user_fact:x", "обычный факт")

    assert m._ticks_since_write == 0


async def test_decay_increments_both_counters(m):
    """decay() инкрементирует оба счётчика (recall и write)."""
    m._ticks_since_recall = 0
    m._ticks_since_write = 0

    await m.decay()

    assert m._ticks_since_recall == 1
    assert m._ticks_since_write == 1


async def test_admission_rejected_does_not_reset_write_counter(m):
    """Отклонённая запись (admission gate) НЕ сбрасывает _ticks_since_write.

    Если запись не состоялась — это не «пользователь писал в память».
    Счётчик должен продолжать расти.
    """
    m._ticks_since_write = 5

    result = await m.remember_user_fact("user_fact:leak", "password: hunter2")

    assert result["stored"] is False
    assert m._ticks_since_write == 5


# ==================== get_stats отдаёт оба счётчика ====================


async def test_get_stats_exposes_both_counters(m):
    """get_stats отдаёт оба счётчика и active_mode."""
    m._ticks_since_recall = 3
    m._ticks_since_write = 7

    stats = m.get_stats()

    assert stats["ticks_since_recall"] == 3
    assert stats["ticks_since_write"] == 7
    assert stats["active_mode"] is False  # оба < 10
