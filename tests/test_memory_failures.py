"""Failure tests for memory subsystem (GradientMemory)."""

import asyncio

import memory.manager as mm
from memory.llm_compressor import LLMCompressor
from memory.manager import GradientMemory


class FakeLLM:
    async def generate(self, prompt, **kwargs):
        return {"response": "LLM SUMMARY"}


async def test_llm_compressor_async():
    """GREEN: async compressor works correctly."""
    compressor = LLMCompressor(FakeLLM())
    long_text = "important fact. " * 50
    result = await compressor.compress(long_text)
    assert result == "LLM SUMMARY"


async def test_gradient_memory_concurrent_access(tmp_path, monkeypatch):
    """GREEN: asyncio locks protect concurrent access."""
    monkeypatch.setattr(mm, "TGS_FILE", str(tmp_path / "tgs.json"))
    monkeypatch.setattr(mm, "HOT_FILE", str(tmp_path / "hot.json"))
    monkeypatch.setattr(mm, "COLD_FILE", str(tmp_path / "cold.json"))

    manager = GradientMemory(data_dir=str(tmp_path))

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


async def test_gradient_memory_decay(tmp_path, monkeypatch):
    """Test decay reduces scores."""
    monkeypatch.setattr(mm, "TGS_FILE", str(tmp_path / "tgs.json"))
    monkeypatch.setattr(mm, "HOT_FILE", str(tmp_path / "hot.json"))
    monkeypatch.setattr(mm, "COLD_FILE", str(tmp_path / "cold.json"))

    manager = GradientMemory(data_dir=str(tmp_path))
    await manager.remember("key1", "value1")

    initial_score = manager._hot["key1"]["score"]
    await manager.decay(count_requests=10)
    new_score = manager._hot["key1"]["score"]

    assert new_score < initial_score


async def test_gradient_memory_session_close(tmp_path, monkeypatch):
    """Test session_close applies penalty."""
    monkeypatch.setattr(mm, "TGS_FILE", str(tmp_path / "tgs.json"))
    monkeypatch.setattr(mm, "HOT_FILE", str(tmp_path / "hot.json"))
    monkeypatch.setattr(mm, "COLD_FILE", str(tmp_path / "cold.json"))

    manager = GradientMemory(data_dir=str(tmp_path))
    await manager.remember("key1", "value1")

    initial_score = manager._hot["key1"]["score"]
    await manager.session_close()
    new_score = manager._hot["key1"]["score"]

    assert new_score < initial_score
    assert manager._session_count == 1
