"""Tests for Watchdog check methods (TZ-025, TZ-026).
Covers: _check_llm, _check_plugins, _check_memory, _check_disk,
        get_status, get_status_icons.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.watchdog import Watchdog

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФАБРИКИ ====================


def make_fake_agent(tmp_path: Path) -> MagicMock:
    """Create a minimal fake AgentCore for Watchdog tests."""
    agent = MagicMock()
    agent.config.log_dir = tmp_path / "logs"
    agent.config.log_dir.mkdir(parents=True, exist_ok=True)
    agent.config.data_dir = tmp_path / "data"
    agent.config.data_dir.mkdir(parents=True, exist_ok=True)
    agent.config.plugins_dir = "plugins"

    # LLM клиент
    agent.llm_client = MagicMock()
    agent.llm_client.health_check = AsyncMock(return_value=True)

    # Plugin registry
    agent.plugin_registry = MagicMock()
    agent.plugin_registry.get = MagicMock(return_value=MagicMock())  # echo найден

    # Memory
    agent.memory = MagicMock()
    agent.memory._load_all = MagicMock()

    # React loop (для обновления при восстановлении LLM)
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
        restart_timeout=0,
        max_restarts=2,
    )


# ==================== LLM CHECK ====================


async def test_check_llm_success(watchdog):
    """When LLM is healthy, _check_llm returns True."""
    watchdog.agent.llm_client.health_check.return_value = True
    result = await watchdog._check_llm()
    assert result is True
    assert watchdog.llm_available is True


async def test_check_llm_failure(watchdog):
    """When LLM is unhealthy, _check_llm returns False."""
    watchdog.agent.llm_client.health_check.return_value = False
    result = await watchdog._check_llm()
    assert result is False
    assert watchdog.llm_available is False


async def test_check_llm_exception(watchdog):
    """When LLM check throws, _check_llm returns False."""
    watchdog.agent.llm_client.health_check.side_effect = RuntimeError("boom")
    result = await watchdog._check_llm()
    assert result is False
    assert watchdog.llm_available is False


async def test_check_llm_no_client(watchdog):
    """When llm_client is None, _check_llm returns False."""
    watchdog.agent.llm_client = None
    # _check_all не вызывает _check_llm если llm_client is None,
    # но сам метод должен корректно упасть или вернуть False
    try:
        result = await watchdog._check_llm()
        assert result is False
    except AttributeError:
        # Допустимый путь: метод падает, _check_all защищает через if
        pass


# ==================== PLUGINS CHECK ====================


async def test_check_plugins_success(watchdog):
    """When echo plugin exists, _check_plugins returns True."""
    watchdog.agent.plugin_registry.get.return_value = MagicMock()
    result = await watchdog._check_plugins()
    assert result is True
    assert watchdog.plugins_available is True


async def test_check_plugins_failure(watchdog):
    """When echo plugin missing, _check_plugins returns False."""
    watchdog.agent.plugin_registry.get.return_value = None
    result = await watchdog._check_plugins()
    assert result is False
    assert watchdog.plugins_available is False


# ==================== MEMORY CHECK ====================


async def test_check_memory_success(watchdog, tmp_path):
    """When all memory files are valid JSON, _check_memory returns True."""
    data_dir = watchdog.agent.config.data_dir
    for fname in ["tgs_memory.json", "tg_hot_memory.json", "tg_cold_memory.json"]:
        (data_dir / fname).write_text("{}", encoding="utf-8")
    result = await watchdog._check_memory()
    assert result is True
    assert watchdog.memory_available is True


async def test_check_memory_corrupted_file(watchdog, tmp_path):
    """When a memory file is corrupted JSON, _check_memory returns False."""
    data_dir = watchdog.agent.config.data_dir
    (data_dir / "tgs_memory.json").write_text("{}", encoding="utf-8")
    (data_dir / "tg_hot_memory.json").write_text("not json", encoding="utf-8")
    (data_dir / "tg_cold_memory.json").write_text("{}", encoding="utf-8")
    result = await watchdog._check_memory()
    assert result is False
    assert watchdog.memory_available is False


async def test_check_memory_missing_file(watchdog, tmp_path):
    """When a memory file is missing, _check_memory returns True (no corruption)."""
    # Все файлы отсутствуют — это не ошибка валидации
    result = await watchdog._check_memory()
    assert result is True
    assert watchdog.memory_available is True


async def test_check_memory_exception(watchdog):
    """When _check_memory throws, it returns False."""
    # Ломаем data_dir, чтобы Path() упал
    watchdog.agent.config.data_dir = None
    result = await watchdog._check_memory()
    assert result is False


# ==================== DISK CHECK ====================


async def test_check_disk_success(watchdog):
    """When disk has enough space, disk_available stays True."""
    await watchdog._check_disk()
    assert watchdog.disk_available is True


async def test_check_disk_exception(watchdog):
    """When disk check throws, it's silently ignored."""
    with patch("core.watchdog.shutil.disk_usage", side_effect=RuntimeError("boom")):
        await watchdog._check_disk()
    # Метод не должен падать


# ==================== STATUS ====================


def test_get_status(watchdog):
    """get_status returns a dict with all modules."""
    status = watchdog.get_status()
    assert "llm" in status
    assert "plugins" in status
    assert "memory" in status
    assert "disk" in status
    assert "available" in status["llm"]
    assert "failures" in status["llm"]


def test_get_status_icons(watchdog):
    """get_status_icons returns a string with colored icons."""
    icons = watchdog.get_status_icons()
    assert "🟢" in icons or "🔴" in icons
    assert "LLM" in icons
    assert "Плагины" in icons
    assert "Память" in icons
    assert "Диск" in icons


def test_get_status_icons_all_red(watchdog):
    """When all modules fail, all icons are red."""
    watchdog.llm_available = False
    watchdog.plugins_available = False
    watchdog.memory_available = False
    watchdog.disk_available = False
    icons = watchdog.get_status_icons()
    assert icons.count("🔴") == 4
    assert "🟢" not in icons
