"""Memory compression chain tests for GradientMemory.
Verifies the compression pipeline: compress_cycle, build_context.
"""

import asyncio
from datetime import datetime

import pytest

import memory.manager as mm


class FakeCompressor:
    """Async compressor stub: value -> deterministic summary."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    async def __call__(self, value):
        self.calls.append(value)
        if self.fail:
            raise RuntimeError("compressor exploded")
        return f"SUMMARY of {value}"


@pytest.fixture
def memory_paths(tmp_path, monkeypatch):
    """Point memory files to a temp dir for isolation."""
    monkeypatch.setattr(mm, "TGS_FILE", str(tmp_path / "tgs.json"))
    monkeypatch.setattr(mm, "HOT_FILE", str(tmp_path / "hot.json"))
    monkeypatch.setattr(mm, "COLD_FILE", str(tmp_path / "cold.json"))
    return tmp_path


async def test_compress_cycle_moves_entry_to_cold(memory_paths):
    """compress_cycle should compress entries in range 5..-4."""
    from memory.manager import GradientMemory

    manager = GradientMemory(data_dir=str(memory_paths))
    await manager.remember("k1", "important fact")

    # Устанавливаем score вручную для попадания в диапазон компрессии
    manager._hot["k1"]["score"] = 3.0

    compressor = FakeCompressor()
    count = await manager.compress_cycle(compressor)

    assert count == 1
    assert compressor.calls == ["important fact"]
    assert "k1" not in manager._hot
    assert "k1" in manager._cold
    assert manager._cold["k1"]["summary"] == "SUMMARY of important fact"

    # recall возвращает value (None для cold), но запись есть в cold
    recalled = await manager.recall("k1")
    assert recalled is None  # value = None для cold записей

    # Проверяем, что запись переместилась в hot с protected=True
    assert "k1" in manager._hot
    assert manager._hot["k1"]["protected"] is True
    assert manager._hot["k1"]["score"] == 10.0  # recall нагревает до 10.0


async def test_compress_cycle_skips_out_of_range(memory_paths):
    """compress_cycle should skip entries outside 5..-4 range."""
    from memory.manager import GradientMemory

    manager = GradientMemory(data_dir=str(memory_paths))
    await manager.remember("k1", "data")

    # score = 25.0 (default) — вне диапазона
    compressor = FakeCompressor()
    count = await manager.compress_cycle(compressor)

    assert count == 0
    assert compressor.calls == []
    assert "k1" in manager._hot


async def test_compress_cycle_skips_protected(memory_paths):
    """compress_cycle should skip protected entries."""
    from memory.manager import GradientMemory

    manager = GradientMemory(data_dir=str(memory_paths))
    await manager.remember("k1", "protected data")
    manager._hot["k1"]["score"] = 3.0
    manager._hot["k1"]["protected"] = True

    compressor = FakeCompressor()
    count = await manager.compress_cycle(compressor)

    assert count == 0
    assert "k1" in manager._hot


async def test_failing_compressor_keeps_entry(memory_paths):
    """If compressor fails, entry should stay in hot."""
    from memory.manager import GradientMemory

    manager = GradientMemory(data_dir=str(memory_paths))
    await manager.remember("k1", "data")
    manager._hot["k1"]["score"] = 3.0

    compressor = FakeCompressor(fail=True)
    count = await manager.compress_cycle(compressor)

    assert count == 0
    assert "k1" in manager._hot
    assert "k1" not in manager._cold


async def test_compress_cycle_runs_through_scheduler(memory_paths):
    """ADR-005: compression is driven by PeriodicScheduler."""
    from core.scheduler import PeriodicScheduler
    from memory.manager import GradientMemory

    manager = GradientMemory(data_dir=str(memory_paths))
    await manager.remember("k", "data")
    manager._hot["k"]["score"] = 3.0

    compressor = FakeCompressor()
    scheduler = PeriodicScheduler()
    scheduler.register("compress", 0.05, lambda: manager.compress_cycle(compressor))
    await scheduler.start()

    for _ in range(50):
        if compressor.calls:
            break
        await asyncio.sleep(0.05)

    await scheduler.stop()
    assert compressor.calls == ["data"]


async def test_build_context_includes_hot_and_cold(memory_paths):
    """build_context should include both hot and cold entries."""
    from memory.manager import GradientMemory

    manager = GradientMemory(data_dir=str(memory_paths))
    await manager.remember("note1", "the weather is sunny today")

    # Добавляем запись в cold вручную
    manager._cold["archive1"] = {
        "value": None,
        "score": -5.0,
        "is_cold": True,
        "protected": False,
        "shield": False,
        "summary": "stable diffusion is a generative model",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }

    context = await asyncio.wait_for(
        manager.build_context("tell me about stable diffusion"), timeout=2.0
    )

    assert "note1" in context or "sunny" in context
    assert "stable diffusion" in context
