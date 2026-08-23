"""Tests for Watchdog auto-recovery logic.
Covers: _handle_llm_failure, _handle_plugins_failure,
        _handle_memory_failure, silence mode, crash reporting.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.watchdog import Watchdog

# ==================== ФАБРИКА FAKE AGENT ====================


def make_fake_agent(tmp_path: Path) -> MagicMock:
    """Create a minimal fake AgentCore for recovery tests."""
    agent = MagicMock()
    agent.config.log_dir = tmp_path / "logs"
    agent.config.log_dir.mkdir(parents=True, exist_ok=True)
    agent.config.data_dir = tmp_path / "data"
    agent.config.data_dir.mkdir(parents=True, exist_ok=True)
    agent.config.plugins_dir = "plugins"

    # LLM клиент
    agent.llm_client = MagicMock()
    agent.llm_client.health_check = AsyncMock(return_value=True)
    agent.llm_client.close = AsyncMock()

    # Plugin registry
    agent.plugin_registry = MagicMock()
    agent.plugin_registry.get = MagicMock(return_value=MagicMock())
    agent.plugin_registry.discover_plugins = MagicMock()

    # Memory
    agent.memory = MagicMock()
    agent.memory._load_all = MagicMock()

    # React loop
    agent.react_loop = MagicMock()

    return agent


@pytest.fixture
def fake_agent(tmp_path):
    return make_fake_agent(tmp_path)


@pytest.fixture
def watchdog(fake_agent):
    return Watchdog(
        agent=fake_agent,
        check_interval=1,
        restart_timeout=0,  # без задержки в тестах
        max_restarts=2,
    )


# ==================== LLM RECOVERY ====================


async def test_llm_recovery_on_first_attempt(watchdog):
    """When LLM recovers after first restart, failures counter resets."""
    watchdog.agent.llm_client.health_check.side_effect = [False, True]

    # Запоминаем старого клиента ДО замены
    old_client = watchdog.agent.llm_client

    with patch("integrations.ollama_client.OllamaClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.health_check = AsyncMock(return_value=True)
        MockClient.return_value = mock_instance

        await watchdog._handle_llm_failure()

    assert watchdog.llm_available is True
    assert watchdog.llm_failures == 0
    MockClient.assert_called_once()
    # close должен быть вызван у СТАРОГО клиента (до замены)
    old_client.close.assert_called_once()


async def test_llm_recovery_replaces_client(watchdog):
    """After recovery, agent must have a new LLM client instance."""
    watchdog.agent.llm_client.health_check.side_effect = [False, True]

    with patch("integrations.ollama_client.OllamaClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.health_check = AsyncMock(return_value=True)
        MockClient.return_value = mock_instance

        await watchdog._handle_llm_failure()

    # Новый клиент должен быть присвоен агенту
    assert watchdog.agent.llm_client is mock_instance
    # React loop должен получить ссылку на новый клиент
    assert watchdog.agent.react_loop.llm is mock_instance


async def test_llm_recovery_updates_react_loop(watchdog):
    """When react_loop exists, it must receive the new LLM client."""
    watchdog.agent.llm_client.health_check.side_effect = [False, True]

    with patch("integrations.ollama_client.OllamaClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.health_check = AsyncMock(return_value=True)
        MockClient.return_value = mock_instance

        await watchdog._handle_llm_failure()

    assert watchdog.agent.react_loop.llm is mock_instance


async def test_llm_recovery_no_react_loop(watchdog):
    """When react_loop is None, recovery must not crash."""
    watchdog.agent.llm_client.health_check.side_effect = [False, True]
    watchdog.agent.react_loop = None

    with patch("integrations.ollama_client.OllamaClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.health_check = AsyncMock(return_value=True)
        MockClient.return_value = mock_instance

        # Не должно упасть
        await watchdog._handle_llm_failure()

    assert watchdog.llm_available is True


async def test_llm_fails_after_max_restarts(watchdog):
    """After max_restarts attempts, LLM must be marked unavailable."""
    # Все попытки падают
    watchdog.agent.llm_client.health_check.return_value = False

    with patch("integrations.ollama_client.OllamaClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.health_check = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        # Первая попытка
        await watchdog._handle_llm_failure()
        # Вторая попытка
        await watchdog._handle_llm_failure()
        # Третья попытка (превышает max_restarts=2)
        await watchdog._handle_llm_failure()

    assert watchdog.llm_available is False
    assert watchdog.llm_failures == 3


async def test_llm_crash_report_generated_on_total_failure(watchdog, tmp_path):
    """When LLM fails completely, crash report must be generated."""
    watchdog.agent.llm_client.health_check.return_value = False

    with patch("integrations.ollama_client.OllamaClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.health_check = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        # Превышаем max_restarts
        for _ in range(3):
            await watchdog._handle_llm_failure()

    # Проверяем, что crash-отчёт создан
    crash_dir = tmp_path / "logs" / "crash_reports"
    assert crash_dir.exists()
    reports = list(crash_dir.glob("**/crash_*.json"))
    assert len(reports) >= 1


async def test_llm_silence_mode_suppresses_duplicates(watchdog):
    """After first crash notification, duplicates must be suppressed."""
    watchdog.agent.llm_client.health_check.return_value = False

    with patch("integrations.ollama_client.OllamaClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.health_check = AsyncMock(return_value=False)
        MockClient.return_value = mock_instance

        # Превышаем max_restarts три раза
        for _ in range(4):
            await watchdog._handle_llm_failure()

    # Уведомление должно быть только одно
    assert watchdog._llm_notified is True
    # Crash-отчётов должно быть не больше одного
    crash_dir = watchdog.agent.config.log_dir / "crash_reports"
    if crash_dir.exists():
        reports = list(crash_dir.glob("**/crash_*.json"))
        assert len(reports) == 1


async def test_llm_recovery_resets_silence_mode(watchdog):
    """After successful recovery, silence mode must reset."""
    # Первая попытка: падает
    watchdog.agent.llm_client.health_check.side_effect = [False, True]

    with patch("integrations.ollama_client.OllamaClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.health_check = AsyncMock(return_value=True)
        MockClient.return_value = mock_instance

        await watchdog._handle_llm_failure()

    # После восстановления режим тишины сброшен
    assert watchdog._llm_notified is False


# ==================== PLUGINS RECOVERY ====================


async def test_plugins_recovery_on_first_attempt(watchdog):
    """When plugins recover after registry reload, failures counter resets."""
    # Сначала echo не найден
    watchdog.agent.plugin_registry.get.return_value = None

    # После discover_plugins — плагин появляется
    def fake_discover(*args, **kwargs):
        watchdog.agent.plugin_registry.get.return_value = MagicMock()

    watchdog.agent.plugin_registry.discover_plugins.side_effect = fake_discover

    await watchdog._handle_plugins_failure()

    assert watchdog.plugins_available is True
    assert watchdog.plugins_failures == 0
    watchdog.agent.plugin_registry.discover_plugins.assert_called_once()


async def test_plugins_fails_after_max_restarts(watchdog):
    """After max_restarts, plugins must be marked unavailable."""
    watchdog.agent.plugin_registry.get.return_value = None

    for _ in range(3):
        await watchdog._handle_plugins_failure()

    assert watchdog.plugins_available is False
    assert watchdog.plugins_failures == 3


async def test_plugins_silence_mode(watchdog):
    """After first crash notification, duplicates must be suppressed."""
    watchdog.agent.plugin_registry.get.return_value = None

    for _ in range(4):
        await watchdog._handle_plugins_failure()

    assert watchdog._plugins_notified is True


# ==================== MEMORY RECOVERY ====================


async def test_memory_recovery_on_reload(watchdog, tmp_path):
    """When memory recovers after _load_all, failures counter resets."""
    # Создаём корректные файлы памяти
    data_dir = watchdog.agent.config.data_dir
    for fname in ["tgs_memory.json", "tg_hot_memory.json", "tg_cold_memory.json"]:
        (data_dir / fname).write_text("{}", encoding="utf-8")

    # Сначала симулируем повреждение
    (data_dir / "tgs_memory.json").write_text("not json", encoding="utf-8")

    # Первая проверка — упадёт
    ok1 = await watchdog._check_memory()
    assert ok1 is False

    # Восстанавливаем файл
    (data_dir / "tgs_memory.json").write_text("{}", encoding="utf-8")

    # Теперь _handle_memory_failure вызовет _load_all и проверит снова
    await watchdog._handle_memory_failure()

    assert watchdog.memory_available is True
    assert watchdog.memory_failures == 0


async def test_memory_fails_after_max_restarts(watchdog, tmp_path):
    """After max_restarts, memory must be marked unavailable."""
    data_dir = watchdog.agent.config.data_dir
    (data_dir / "tgs_memory.json").write_text("not json", encoding="utf-8")

    for _ in range(3):
        await watchdog._handle_memory_failure()

    assert watchdog.memory_available is False
    assert watchdog.memory_failures == 3


# ==================== STATUS AFTER RECOVERY ====================


def test_status_reflects_recovery(watchdog):
    """get_status must reflect current module states after recovery."""
    watchdog.llm_available = True
    watchdog.plugins_available = False
    watchdog.memory_available = True
    watchdog.disk_available = True

    status = watchdog.get_status()
    assert status["llm"]["available"] is True
    assert status["plugins"]["available"] is False
    assert status["memory"]["available"] is True
    assert status["disk"]["available"] is True


def test_status_icons_reflect_recovery(watchdog):
    """get_status_icons must show correct icons after recovery."""
    watchdog.llm_available = True
    watchdog.plugins_available = False
    watchdog.memory_available = True
    watchdog.disk_available = True

    icons = watchdog.get_status_icons()
    assert icons.count("🟢") == 3
    assert icons.count("🔴") == 1
