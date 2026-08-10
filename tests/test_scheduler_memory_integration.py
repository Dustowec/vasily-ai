"""Integration tests: PeriodicScheduler + MemoryManager (ADR-005, TZ-022)."""

import asyncio

import pytest

import memory.manager as mm
from core.scheduler import PeriodicScheduler


class FakeCompressor:
    def __init__(self):
        self.calls = []

    async def __call__(self, value):
        self.calls.append(value)
        return f"SUM of {value}"


@pytest.fixture
def memory_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "HOT_FILE", str(tmp_path / "hot.json"))
    monkeypatch.setattr(mm, "COLD_FILE", str(tmp_path / "cold.json"))
    monkeypatch.setattr(mm, "HOT_RETENTION_HOURS", 0)
    return tmp_path


async def test_memory_compression_cycle_runs_once(memory_paths):
    manager = mm.MemoryManager()
    await manager.remember("k", "data")
    compressor = FakeCompressor()

    await manager.compress_cycle(compressor)

    assert compressor.calls == ["data"]
    assert "k" in manager._cold_data


async def test_scheduler_drives_memory_compression(memory_paths):
    manager = mm.MemoryManager()
    await manager.remember("k", "data")
    compressor = FakeCompressor()

    scheduler = PeriodicScheduler()
    scheduler.register(
        "memory_compression",
        0.05,
        lambda: manager.compress_cycle(compressor),
    )
    await scheduler.start()
    for _ in range(50):
        if compressor.calls:
            break
        await asyncio.sleep(0.02)
    await scheduler.stop()

    assert compressor.calls == ["data"]
    assert "k" in manager._cold_data
