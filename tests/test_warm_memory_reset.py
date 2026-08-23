"""Tests for Warm memory reset via PeriodicScheduler (GradientMemory)."""

import asyncio

import pytest

import memory.manager as mm
from core.scheduler import PeriodicScheduler
from memory.manager import GradientMemory


@pytest.fixture
def memory_paths(tmp_path, monkeypatch):
    """Isolate memory files in a temporary directory."""
    monkeypatch.setattr(mm, "TGS_FILE", str(tmp_path / "tgs.json"))
    monkeypatch.setattr(mm, "HOT_FILE", str(tmp_path / "hot.json"))
    monkeypatch.setattr(mm, "COLD_FILE", str(tmp_path / "cold.json"))
    return tmp_path


async def test_warm_memory_reset_task_clears_dialogue(memory_paths):
    """PeriodicScheduler must call forget on dialogue:last after the interval.

    Note: forget() doesn't delete the entry completely — it moves it to cold
    or reduces score. We verify the entry was processed by checking it's in cold.
    """
    manager = GradientMemory(data_dir=str(memory_paths))
    await manager.remember("dialogue:last", {"user": "hi", "assistant": "hello"})

    # Initially in hot
    assert "dialogue:last" in manager._hot

    scheduler = PeriodicScheduler()
    scheduler.register(
        "dialogue_reset",
        0.05,
        lambda: manager.forget("dialogue:last"),
    )
    await scheduler.start()

    # Wait for scheduler to execute
    for _ in range(50):
        if "dialogue:last" in manager._cold:
            break
        await asyncio.sleep(0.05)

    await scheduler.stop()

    # Entry should be moved to cold (forget reduces score and moves to cold)
    assert "dialogue:last" in manager._cold
    assert "dialogue:last" not in manager._hot
