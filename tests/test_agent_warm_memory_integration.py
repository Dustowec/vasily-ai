"""Integration test: AgentCore registers dialogue_reset task."""

from unittest.mock import patch

import pytest

import memory.manager as mm
from core.agent import DIALOGUE_RESET_INTERVAL_SECONDS, AgentCore
from core.config import Config


@pytest.fixture
def memory_paths(tmp_path, monkeypatch):
    """Isolate memory files in a temporary directory."""
    monkeypatch.setattr(mm, "HOT_FILE", str(tmp_path / "hot.json"))
    monkeypatch.setattr(mm, "COLD_FILE", str(tmp_path / "cold.json"))
    return tmp_path


def test_dialogue_reset_interval_constant():
    """Warm memory reset interval must be 30 minutes."""
    assert DIALOGUE_RESET_INTERVAL_SECONDS == 30 * 60


async def test_agent_registers_dialogue_reset_in_scheduler(memory_paths):
    """AgentCore must register dialogue_reset alongside memory_compression."""
    config = Config.load()
    agent = AgentCore(config)

    registered_tasks = {}

    class FakeScheduler:
        def register(self, name, interval, coro_factory):
            registered_tasks[name] = interval

        async def start(self):
            pass

        async def stop(self):
            pass

    async def fake_cli_loop():
        agent.running = False

    agent._cli_loop = fake_cli_loop
    agent.llm_client = None

    with patch("core.agent.PeriodicScheduler", FakeScheduler):
        await agent.run()

    assert "dialogue_reset" in registered_tasks
    assert registered_tasks["dialogue_reset"] == DIALOGUE_RESET_INTERVAL_SECONDS
    assert "memory_compression" in registered_tasks
