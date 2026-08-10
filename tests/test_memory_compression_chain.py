"""Memory compression chain tests (Coverage Hardening, file 2 of 3).

Verifies the P1-1 async compression pipeline end-to-end:
compress_to_cold, compress_all_expired, background worker, build_context.
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
    monkeypatch.setattr(mm, "HOT_FILE", str(tmp_path / "hot.json"))
    monkeypatch.setattr(mm, "COLD_FILE", str(tmp_path / "cold.json"))
    return tmp_path


async def test_compress_to_cold_moves_entry(memory_paths):
    manager = mm.MemoryManager()
    await manager.remember("k1", "important fact")
    compressor = FakeCompressor()

    ok = await manager.compress_to_cold("k1", compressor)

    assert ok is True
    assert compressor.calls == ["important fact"]
    assert "k1" not in manager.hot.get_all_entries()
    assert manager._cold_data["k1"]["summary"] == "SUMMARY of important fact"
    # recall now serves the cold summary
    assert await manager.recall("k1") == "SUMMARY of important fact"


async def test_compress_to_cold_missing_key(memory_paths):
    manager = mm.MemoryManager()
    compressor = FakeCompressor()

    ok = await manager.compress_to_cold("nope", compressor)

    assert ok is False
    assert compressor.calls == []


async def test_compress_all_expired_compresses_expired(memory_paths, monkeypatch):
    monkeypatch.setattr(mm, "HOT_RETENTION_HOURS", 0)
    manager = mm.MemoryManager()
    await manager.remember("a", "value-a")
    await manager.remember("b", "value-b")
    compressor = FakeCompressor()

    count = await manager.compress_all_expired(compressor)

    assert count == 2
    assert len(compressor.calls) == 2
    assert "a" not in manager.hot.get_all_entries()
    assert "b" not in manager.hot.get_all_entries()
    assert set(manager._cold_data.keys()) == {"a", "b"}


async def test_compress_all_expired_skips_fresh(memory_paths):
    manager = mm.MemoryManager()
    await manager.remember("fresh", "value-fresh")
    compressor = FakeCompressor()

    count = await manager.compress_all_expired(compressor)

    assert count == 0
    assert compressor.calls == []
    assert "fresh" in manager.hot.get_all_entries()


async def test_failing_compressor_keeps_entry(memory_paths, monkeypatch):
    monkeypatch.setattr(mm, "HOT_RETENTION_HOURS", 0)
    manager = mm.MemoryManager()
    await manager.remember("k1", "data")
    compressor = FakeCompressor(fail=True)

    ok = await manager.compress_to_cold("k1", compressor)

    assert ok is False
    assert "k1" in manager.hot.get_all_entries()
    assert "k1" not in manager._cold_data


async def test_compress_cycle_runs_through_scheduler(memory_paths, monkeypatch):
    """ADR-005: compression is driven by PeriodicScheduler, not by own worker."""
    monkeypatch.setattr(mm, "HOT_RETENTION_HOURS", 0)
    manager = mm.MemoryManager()
    await manager.remember("k", "data")
    compressor = FakeCompressor()

    from core.scheduler import PeriodicScheduler

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
    manager = mm.MemoryManager()
    await manager.remember("note1", "the weather is sunny today")
    manager._cold_data["archive1"] = {
        "summary": "stable diffusion is a generative model",
        "compressed_at": datetime.now().isoformat(),
        "original_created": datetime.now().isoformat(),
    }

    # wait_for guards against the lock-deadlock regression
    context = await asyncio.wait_for(
        manager.build_context("tell me about stable diffusion"), timeout=2.0
    )

    assert "note1" in context
    assert "stable diffusion" in context
