"""Tests for AgentCore.

Covers: initialize, handle_request, shutdown, get_metrics,
        command handling, error handling.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent import AgentCore
from core.config import Config


@pytest.fixture
def config(tmp_path) -> Config:
    """Create a test Config with temporary directories."""
    cfg = Config()
    cfg.log_dir = tmp_path / "logs"
    cfg.data_dir = tmp_path / "data"
    cfg.plugins_dir = "plugins"
    cfg.llm_url = "http://localhost:11434"
    cfg.llm_model = "test-model"
    cfg.llm_max_retries = 1
    cfg.llm_num_ctx = 4096
    cfg.llm_safety_margin = 100
    cfg.max_react_iterations = 3
    cfg.max_tool_calls_per_tool = 2
    cfg.log_preview_length = 50
    cfg.request_timeout = 30.0
    cfg.watchdog_enabled = False  # Отключаем для тестов
    return cfg


@pytest.fixture
def agent(config):
    """Create an AgentCore instance with mocks."""
    with patch("core.agent.PluginRegistry") as MockRegistry:
        mock_registry = MagicMock()
        mock_registry.discover_plugins = MagicMock()
        mock_registry.list_tools = MagicMock(return_value=["echo", "web_search"])
        mock_registry.__len__ = MagicMock(return_value=2)
        MockRegistry.return_value = mock_registry

        with patch("core.agent.OllamaClient") as MockClient:
            mock_client = MagicMock()
            mock_client.health_check = AsyncMock(return_value=True)
            mock_client.close = AsyncMock()
            MockClient.return_value = mock_client

            with patch("core.agent.ensure_ollama_running", AsyncMock(return_value=True)):
                with patch("core.agent.GradientMemory") as MockMemory:
                    mock_memory = MagicMock()
                    mock_memory.build_context = AsyncMock(return_value="test context")
                    mock_memory.remember = AsyncMock()
                    mock_memory.forget = AsyncMock(return_value=True)
                    mock_memory.forget_all = AsyncMock(return_value=True)
                    mock_memory.decay = AsyncMock()
                    mock_memory.get_stats = MagicMock(
                        return_value={"tgs": 0, "hot": 0, "cold": 0, "total": 0}
                    )
                    mock_memory.__len__ = MagicMock(return_value=0)
                    MockMemory.return_value = mock_memory

                    agent = AgentCore(config)
                    yield agent


# ==================== TEST INITIALIZE ====================


async def test_initialize_success(agent):
    """AgentCore.initialize should set up all subsystems."""
    with patch.object(agent, "health_check", AsyncMock(return_value={"overall": "OK"})):
        await agent.initialize()
    assert agent.llm_client is not None
    assert agent.react_loop is not None
    assert agent.plugin_registry is not None


# ==================== TEST HANDLE_REQUEST ====================


async def test_handle_request_status(agent):
    """handle_request should return status metrics."""
    await agent.initialize()
    response = await agent.handle_request({"text": "status"})
    assert response["status"] == "success"
    assert "metrics" in response
    assert "memory_stats" in response


async def test_handle_request_help(agent):
    """handle_request should return help message."""
    await agent.initialize()
    response = await agent.handle_request({"text": "help"})
    assert response["status"] == "success"
    assert "Available commands" in response["message"]


async def test_handle_request_forget_topic(agent):
    """handle_request should handle 'забудь <тема>'."""
    await agent.initialize()
    response = await agent.handle_request({"text": "забудь самурай"})
    assert response["status"] == "success"
    assert "забыта" in response["message"]


async def test_handle_request_forget_all(agent):
    """handle_request should handle 'забудь всё' and confirm."""
    await agent.initialize()
    response = await agent.handle_request({"text": "забудь всё"})
    assert response["status"] == "error"
    assert "подтверждение" in response["message"]


async def test_handle_request_forget_all_confirm(agent):
    """handle_request should handle 'забудь всё да'."""
    await agent.initialize()
    response = await agent.handle_request({"text": "забудь всё да"})
    assert response["status"] == "success"
    assert "очищена" in response["message"]


async def test_handle_request_empty_topic(agent):
    """handle_request should reject empty topic for forget."""
    await agent.initialize()
    # Подменяем react_loop.run, но он не должен вызываться
    # Если он вызовется — тест упадёт, потому что мы не дали ему возвращаемое значение
    agent.react_loop.run = AsyncMock()
    response = await agent.handle_request({"text": "забудь "})
    # Если команда обработалась до react_loop, response будет успешным с ошибкой
    # Если нет — react_loop.run вызовется и упадёт, тест не дойдёт до assert
    assert response["status"] == "error"
    assert "тему" in response["message"]
    # Проверяем, что react_loop.run НЕ вызывался
    agent.react_loop.run.assert_not_called()


async def test_handle_request_no_react_loop(agent):
    """handle_request should return error if react_loop is None."""
    # Не вызываем initialize, react_loop = None
    response = await agent.handle_request({"text": "hello"})
    assert response["status"] == "error"
    assert "ReAct loop" in response["message"]


async def test_handle_request_llm_unavailable(agent):
    """handle_request should handle LLMUnavailableError gracefully."""
    await agent.initialize()
    agent.react_loop.run = AsyncMock(side_effect=Exception("LLMUnavailableError"))

    response = await agent.handle_request({"text": "hello"})
    assert response["status"] == "error"
    assert response["message"] is not None


async def test_handle_request_generic_error(agent):
    """handle_request should handle generic exceptions."""
    await agent.initialize()
    agent.react_loop.run = AsyncMock(side_effect=RuntimeError("something went wrong"))

    response = await agent.handle_request({"text": "hello"})
    assert response["status"] == "error"
    assert "something went wrong" in response["message"]


# ==================== TEST GET_METRICS ====================


def test_get_metrics(agent):
    """get_metrics should return a dict with metrics."""
    metrics = agent.get_metrics()
    assert "uptime_seconds" in metrics
    assert "requests_count" in metrics
    assert "errors_count" in metrics
    assert "plugins_loaded" in metrics
    assert "memory_entries" in metrics


# ==================== TEST SHUTDOWN ====================


async def test_shutdown(agent):
    """shutdown should clean up resources."""
    await agent.initialize()
    agent.llm_client = AsyncMock()
    agent.llm_client.close = AsyncMock()

    await agent.shutdown()
    agent.llm_client.close.assert_called_once()
    assert agent.running is False


# ==================== TEST CANCEL_ACTIVE_REQUEST ====================


async def test_cancel_active_request(agent):
    """cancel_active_request should cancel the active task."""
    task = asyncio.create_task(asyncio.sleep(10))
    agent._active_request_task = task
    assert agent.cancel_active_request() is True
    await asyncio.sleep(0.05)
    assert task.cancelled() is True


def test_cancel_active_request_none(agent):
    """cancel_active_request should return False if no active request."""
    agent._active_request_task = None
    assert agent.cancel_active_request() is False


# ==================== TEST DIALOGUE_STORAGE ====================


async def test_store_dialogue_success(agent):
    """_store_dialogue should save dialogue to memory."""
    await agent.initialize()
    result = {"status": "success", "answer": "test answer"}
    await agent._store_dialogue("user question", result)
    agent.memory.remember.assert_called_once()


async def test_store_dialogue_skips_error(agent):
    """_store_dialogue should skip if status is error."""
    await agent.initialize()
    result = {"status": "error", "answer": "error message"}
    await agent._store_dialogue("user question", result)
    agent.memory.remember.assert_not_called()


async def test_store_dialogue_skips_empty_answer(agent):
    """_store_dialogue should skip if answer is empty."""
    await agent.initialize()
    result = {"status": "success", "answer": ""}
    await agent._store_dialogue("user question", result)
    agent.memory.remember.assert_not_called()
