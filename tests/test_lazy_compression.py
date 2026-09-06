"""Tests for lazy per-request memory compression (replaces PeriodicScheduler)."""

import pytest

from core.config import Config


class _StubMemory:
    """Minimal GradientMemory stand-in for trigger-logic tests."""

    def __init__(self, has_candidates: bool):
        self._has = has_candidates
        self.compress_calls = 0

    def has_compression_candidates(self) -> bool:
        return self._has

    async def compress_cycle(self, compressor):
        self.compress_calls += 1
        return 1


def _make_agent():
    from core.agent import AgentCore

    agent = AgentCore(Config())
    agent.memory = _StubMemory(has_candidates=True)
    agent.llm_client = None
    return agent


@pytest.mark.asyncio
async def test_trigger_runs_compression_when_candidates_exist():
    agent = _make_agent()
    agent._maybe_compress_memory()
    assert agent._compression_task is not None
    await agent._compression_task
    assert agent.memory.compress_calls == 1


@pytest.mark.asyncio
async def test_trigger_skips_when_no_candidates():
    agent = _make_agent()
    agent.memory = _StubMemory(has_candidates=False)
    agent._maybe_compress_memory()
    assert agent._compression_task is None
    assert agent.memory.compress_calls == 0


@pytest.mark.asyncio
async def test_trigger_does_not_stack_parallel_compressions():
    agent = _make_agent()
    agent._maybe_compress_memory()
    first = agent._compression_task
    agent._maybe_compress_memory()
    assert agent._compression_task is first
    await first
    assert agent.memory.compress_calls == 1


@pytest.mark.asyncio
async def test_has_compression_candidates(tmp_path):
    from memory.manager import GradientMemory

    memory = GradientMemory(data_dir=str(tmp_path))
    assert not memory.has_compression_candidates()

    await memory.remember("k", "x" * 200, complex_query=True)  # score 40
    assert not memory.has_compression_candidates()

    memory._hot["k"]["score"] = 0.0  # inside compression range
    assert memory.has_compression_candidates()

    memory._hot["k"]["protected"] = True
    assert not memory.has_compression_candidates()
