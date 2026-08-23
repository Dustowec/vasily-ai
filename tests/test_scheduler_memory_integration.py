"""Integration tests: PeriodicScheduler + GradientMemory (ADR-005, TZ-022)."""

import asyncio

import pytest

import memory.manager as mm
from core.scheduler import PeriodicScheduler
from memory.manager import GradientMemory


class FakeCompressor:
    """Async compressor stub: value -> deterministic summary."""

    def __init__(self):
        self.calls = []

    async def __call__(self, value):
        self.calls.append(value)
        return f"SUM of {value}"


@pytest.fixture
def memory_paths(tmp_path, monkeypatch):
    """Isolate memory files in a temporary directory."""
    monkeypatch.setattr(mm, "TGS_FILE", str(tmp_path / "tgs.json"))
    monkeypatch.setattr(mm, "HOT_FILE", str(tmp_path / "hot.json"))
    monkeypatch.setattr(mm, "COLD_FILE", str(tmp_path / "cold.json"))
    return tmp_path


async def test_memory_compression_cycle_runs_once(memory_paths):
    """compress_cycle should compress one entry in the 5..-4 range."""
    manager = GradientMemory(data_dir=str(memory_paths))
    await manager.remember("k", "data")

    # Put the entry into the compression range manually (5..-4)
    manager._hot["k"]["score"] = 3.0

    compressor = FakeCompressor()
    await manager.compress_cycle(compressor)

    assert compressor.calls == ["data"]
    assert "k" in manager._cold


async def test_scheduler_drives_memory_compression(memory_paths):
    """PeriodicScheduler must drive memory compression in the background."""
    manager = GradientMemory(data_dir=str(memory_paths))
    await manager.remember("k", "data")

    # Put the entry into the compression range manually (5..-4)
    manager._hot["k"]["score"] = 3.0

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
    assert "k" in manager._cold
