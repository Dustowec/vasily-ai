"""Tests for HealthChecker.

Covers: _check_plugins, _check_llm, _check_directories, _check_memory,
        run_all, print_report.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from core.config import Config
from core.health_check import HealthChecker


@pytest.fixture
def mock_config(tmp_path) -> Config:
    """Create a mock Config with temporary directories."""
    config = Config()
    config.log_dir = tmp_path / "logs"
    config.data_dir = tmp_path / "data"
    config.llm_url = "http://localhost:11434"
    config.llm_max_retries = 1
    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def mock_plugin_registry():
    """Create a mock PluginRegistry with 3 plugins."""
    registry = MagicMock()
    registry.__len__ = MagicMock(return_value=3)
    registry.list_tools = MagicMock(return_value=["plugin1", "plugin2", "plugin3"])
    return registry


@pytest.fixture
def mock_memory_manager():
    """Create a mock MemoryManager."""
    memory = MagicMock()
    memory.__len__ = MagicMock(return_value=5)
    return memory


# ==================== TEST _CHECK_PLUGINS ====================


async def test_check_plugins_success(mock_config, mock_plugin_registry):
    """When plugins are loaded, _check_plugins returns OK."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=mock_plugin_registry,
        memory_manager=None,
    )
    result = await checker._check_plugins()
    assert result["status"] == "OK"
    assert result["count"] == 3
    assert result["plugins"] == ["plugin1", "plugin2", "plugin3"]


async def test_check_plugins_no_registry(mock_config):
    """When plugin_registry is None, _check_plugins returns FAIL."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=None,
        memory_manager=None,
    )
    result = await checker._check_plugins()
    assert result["status"] == "FAIL"
    assert "PluginRegistry not provided" in result["error"]


async def test_check_plugins_no_plugins(mock_config):
    """When no plugins loaded, _check_plugins returns FAIL."""
    empty_registry = MagicMock()
    empty_registry.__len__ = MagicMock(return_value=0)

    checker = HealthChecker(
        config=mock_config,
        plugin_registry=empty_registry,
        memory_manager=None,
    )
    result = await checker._check_plugins()
    assert result["status"] == "FAIL"
    assert "No plugins loaded" in result["error"]


async def test_check_plugins_exception(mock_config):
    """When _check_plugins throws, it returns FAIL."""
    broken_registry = MagicMock()
    broken_registry.__len__ = MagicMock(side_effect=RuntimeError("boom"))

    checker = HealthChecker(
        config=mock_config,
        plugin_registry=broken_registry,
        memory_manager=None,
    )
    result = await checker._check_plugins()
    assert result["status"] == "FAIL"
    assert "boom" in result["error"]


# ==================== TEST _CHECK_LLM ====================


async def test_check_llm_success(mock_config):
    """When LLM responds with 200, _check_llm returns OK."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=None,
        memory_manager=None,
    )

    # Создаём мок для response
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession.get", return_value=mock_resp):
        result = await checker._check_llm()
        assert result["status"] == "OK"
        assert result["url"] == mock_config.llm_url


async def test_check_llm_http_error(mock_config):
    """When LLM returns non-200, _check_llm returns FAIL."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=None,
        memory_manager=None,
    )

    mock_resp = AsyncMock()
    mock_resp.status = 503
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession.get", return_value=mock_resp):
        result = await checker._check_llm()
        assert result["status"] == "FAIL"
        assert result["http_status"] == 503


async def test_check_llm_retry_success(mock_config):
    """When LLM fails first attempt but succeeds on retry, returns OK."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=None,
        memory_manager=None,
    )

    call_count = 0

    def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_resp = AsyncMock()
        if call_count == 1:
            # Первый вызов — ошибка
            mock_resp.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("connection error"))
        else:
            # Второй вызов — успех
            mock_resp.status = 200
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        return mock_resp

    with patch("aiohttp.ClientSession.get", side_effect=mock_get):
        result = await checker._check_llm()
        assert result["status"] == "OK"


async def test_check_llm_all_retries_exhausted(mock_config):
    """When all retries fail, _check_llm returns FAIL with DEGRADED mode."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=None,
        memory_manager=None,
    )

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("connection error"))
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession.get", return_value=mock_resp):
        result = await checker._check_llm()
        assert result["status"] == "FAIL"
        assert result["mode"] == "DEGRADED"
        assert "LLM unavailable" in result["error"]


# ==================== TEST _CHECK_DIRECTORIES ====================


async def test_check_directories_success(mock_config):
    """When directories exist, _check_directories returns OK."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=None,
        memory_manager=None,
    )
    result = await checker._check_directories()
    assert result["status"] == "OK"
    assert result["details"]["logs"] == "OK"
    assert result["details"]["data"] == "OK"


async def test_check_directories_creates_missing(mock_config, tmp_path):
    """When directories missing, _check_directories creates them."""
    import shutil

    shutil.rmtree(mock_config.log_dir, ignore_errors=True)
    shutil.rmtree(mock_config.data_dir, ignore_errors=True)

    checker = HealthChecker(
        config=mock_config,
        plugin_registry=None,
        memory_manager=None,
    )
    result = await checker._check_directories()
    assert result["status"] == "OK"
    assert mock_config.log_dir.exists()
    assert mock_config.data_dir.exists()


# ==================== TEST _CHECK_MEMORY ====================


async def test_check_memory_success(mock_config, mock_memory_manager):
    """When memory has entries, _check_memory returns OK."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=None,
        memory_manager=mock_memory_manager,
    )
    result = await checker._check_memory()
    assert result["status"] == "OK"
    assert result["entries"] == 5


async def test_check_memory_no_manager(mock_config):
    """When memory_manager is None, _check_memory returns FAIL."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=None,
        memory_manager=None,
    )
    result = await checker._check_memory()
    assert result["status"] == "FAIL"
    assert "MemoryManager not provided" in result["error"]


async def test_check_memory_exception(mock_config):
    """When _check_memory throws, it returns FAIL."""
    broken_memory = MagicMock()
    broken_memory.__len__ = MagicMock(side_effect=RuntimeError("boom"))

    checker = HealthChecker(
        config=mock_config,
        plugin_registry=None,
        memory_manager=broken_memory,
    )
    result = await checker._check_memory()
    assert result["status"] == "FAIL"
    assert "boom" in result["error"]


# ==================== TEST RUN_ALL ====================


async def test_run_all_all_ok(mock_config, mock_plugin_registry, mock_memory_manager):
    """When all checks pass, run_all returns OK overall."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=mock_plugin_registry,
        memory_manager=mock_memory_manager,
    )

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession.get", return_value=mock_resp):
        report = await checker.run_all()
        assert report["plugins"]["status"] == "OK"
        assert report["llm"]["status"] == "OK"
        assert report["directories"]["status"] == "OK"
        assert report["memory"]["status"] == "OK"
        assert report["overall"] == "OK"


async def test_run_all_partial_failure(mock_config, mock_plugin_registry, mock_memory_manager):
    """When some checks fail, run_all returns DEGRADED overall."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=mock_plugin_registry,
        memory_manager=mock_memory_manager,
    )

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("connection error"))
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession.get", return_value=mock_resp):
        report = await checker.run_all()
        assert report["plugins"]["status"] == "OK"
        assert report["llm"]["status"] == "FAIL"
        assert report["directories"]["status"] == "OK"
        assert report["memory"]["status"] == "OK"
        assert report["overall"] == "DEGRADED"


# ==================== TEST PRINT_REPORT ====================


def test_print_report(capsys, mock_config):
    """print_report should output colored health report."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=None,
        memory_manager=None,
    )

    report = {
        "plugins": {"status": "OK", "count": 3},
        "llm": {"status": "FAIL", "mode": "DEGRADED"},
        "directories": {"status": "OK"},
        "memory": {"status": "OK", "entries": 5},
        "overall": "DEGRADED",
    }

    checker.print_report(report)

    captured = capsys.readouterr()
    assert "PLUGINS" in captured.out
    assert "LLM" in captured.out
    assert "OVERALL" in captured.out


def test_print_report_handles_missing_fields(capsys, mock_config):
    """print_report should handle missing fields gracefully."""
    checker = HealthChecker(
        config=mock_config,
        plugin_registry=None,
        memory_manager=None,
    )

    report = {
        "plugins": {"status": "UNKNOWN"},
        "llm": {},
        "directories": {},
        "memory": {},
        "overall": "UNKNOWN",
    }

    checker.print_report(report)

    captured = capsys.readouterr()
    assert "PLUGINS" in captured.out
    assert "LLM" in captured.out
    assert "OVERALL" in captured.out
