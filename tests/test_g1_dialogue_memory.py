"""Tests for G-1: dialogue persistence connected to agent cycle."""

import pytest

from core.agent import AgentCore
from core.config import Config


class FakeReActLoop:
    """Fake ReAct loop that captures input arguments."""

    def __init__(self):
        self.calls = []

    async def run(self, user_text, prompt_type="default", memory_context=""):
        self.calls.append(
            {
                "user_text": user_text,
                "prompt_type": prompt_type,
                "memory_context": memory_context,
            }
        )
        return {
            "status": "success",
            "answer": "test answer",
            "iterations": 1,
            "steps": [],
        }


@pytest.fixture
def memory_paths(tmp_path, monkeypatch):
    """Isolate memory files in a temporary directory."""
    # Сохраняем оригинальный метод загрузки
    original_load = Config.load

    # Создаём обёртку, которая принудительно устанавливает data_dir во временную папку
    def mock_load(*args, **kwargs):
        cfg = original_load(*args, **kwargs)
        cfg.data_dir = tmp_path
        return cfg

    # Подменяем метод загрузки для всех тестов, использующих эту фикстуру
    monkeypatch.setattr(Config, "load", mock_load)
    return tmp_path


async def test_handle_request_stores_last_dialogue(memory_paths):
    """Agent must store user request and assistant answer into memory."""
    config = Config.load()
    agent = AgentCore(config)
    agent.react_loop = FakeReActLoop()

    response = await agent.handle_request({"id": "1", "text": "Привет"})
    assert response["status"] == "success"

    stored = await agent.memory.recall("dialogue:last")
    assert stored is not None
    assert stored["user"] == "Привет"
    assert stored["assistant"] == "test answer"

    await agent.shutdown()


async def test_handle_request_passes_memory_context(memory_paths):
    """Second request must receive previous dialogue as memory_context."""
    config = Config.load()
    agent = AgentCore(config)
    react_loop = FakeReActLoop()
    agent.react_loop = react_loop

    await agent.handle_request({"id": "1", "text": "Первый запрос"})
    await agent.handle_request({"id": "2", "text": "Второй запрос"})

    assert len(react_loop.calls) == 2

    first_context = react_loop.calls[0]["memory_context"]
    second_context = react_loop.calls[1]["memory_context"]

    # Первый запрос должен получить пустой контекст (память чистая)
    assert first_context == ""

    # Второй запрос должен получить контекст с первым запросом и ответом
    assert "Первый запрос" in second_context
    assert "test answer" in second_context

    await agent.shutdown()
