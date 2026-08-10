"""Tests for Warm memory reset via PeriodicScheduler."""

import asyncio

import pytest

import memory.manager as mm
from core.scheduler import PeriodicScheduler


@pytest.fixture
def memory_paths(tmp_path, monkeypatch):
    """Isolate memory files in a temporary directory."""
    monkeypatch.setattr(mm, "HOT_FILE", str(tmp_path / "hot.json"))
    monkeypatch.setattr(mm, "COLD_FILE", str(tmp_path / "cold.json"))
    return tmp_path


async def test_warm_memory_reset_task_clears_dialogue(memory_paths):
    """PeriodicScheduler must clear dialogue:last after the interval."""
    manager = mm.MemoryManager()
    await manager.remember("dialogue:last", {"user": "hi", "assistant": "hello"})

    assert await manager.recall("dialogue:last") is not None

    scheduler = PeriodicScheduler()
    scheduler.register(
        "dialogue_reset",
        0.05,
        lambda: manager.forget("dialogue:last"),
    )
    await scheduler.start()

    for _ in range(50):
        if await manager.recall("dialogue:last") is None:
            break
        await asyncio.sleep(0.05)

    await scheduler.stop()

    assert await manager.recall("dialogue:last") is None
