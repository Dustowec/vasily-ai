"""Failure tests for memory subsystem (Sprint 1, Step 0)."""

import asyncio

import memory.manager as mm
from memory.llm_compressor import LLMCompressor


class FakeLLM:
    async def generate(self, prompt, **kwargs):
        return {"response": "LLM SUMMARY"}


async def test_llm_compressor_async():
    """GREEN now: async compressor works correctly."""
    compressor = LLMCompressor(FakeLLM())
    long_text = "important fact. " * 50
    result = await compressor.compress(long_text)
    assert result == "LLM SUMMARY"


async def test_memory_manager_concurrent_access(tmp_path, monkeypatch):
    """GREEN now: asyncio.Lock protects concurrent access."""
    monkeypatch.setattr(mm, "HOT_FILE", str(tmp_path / "hot.json"))
    monkeypatch.setattr(mm, "COLD_FILE", str(tmp_path / "cold.json"))
    manager = mm.MemoryManager()

    async def writer(n):
        for i in range(20):
            await manager.remember(f"key-{n}-{i}", f"value-{n}-{i}")
            await asyncio.sleep(0)

    async def reader():
        for i in range(20):
            await manager.recall(f"key-0-{i}")
            await asyncio.sleep(0)

    await asyncio.gather(writer(0), writer(1), reader())
    assert len(manager) >= 40
