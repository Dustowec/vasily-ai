"""Tests for logging_config module.

Covers: setup_logging, get_logger, LazyLogger,
        alert_level_processor, sanitize_processor,
        rotation settings.
"""

from unittest.mock import patch

import pytest

from core.logging_config import (
    LazyLogger,
    alert_level_processor,
    get_logger,
    reset_sanitize_config_cache,
    sanitize_processor,
    setup_logging,
)


@pytest.fixture
def log_dir(tmp_path):
    """Create a temporary log directory."""
    return tmp_path / "logs"


def test_setup_logging_creates_files(log_dir):
    """setup_logging should create all log files."""
    setup_logging(log_dir=log_dir, level="INFO", json_logs=True)
    assert (log_dir / "core.log").exists()
    assert (log_dir / "interaction.log").exists()
    assert (log_dir / "plugins.log").exists()
    assert (log_dir / "llm.log").exists()
    assert (log_dir / "vasily.log").exists()


def test_setup_logging_creates_directory(tmp_path):
    """setup_logging should create log directory if it doesn't exist."""
    log_dir = tmp_path / "new" / "nested" / "logs"
    setup_logging(log_dir=log_dir, level="INFO", json_logs=True)
    assert log_dir.exists()


def test_get_logger_returns_lazy_logger():
    """get_logger should return a LazyLogger proxy."""
    logger = get_logger("core", "TestModule")
    assert isinstance(logger, LazyLogger)
    assert logger.category == "core"
    assert logger.name == "TestModule"


def test_get_logger_without_name():
    """get_logger should work without module name."""
    logger = get_logger("plugins")
    assert isinstance(logger, LazyLogger)
    assert logger.category == "plugins"
    assert logger.name is None


def test_lazy_logger_creates_real_on_first_call(log_dir):
    """LazyLogger should create real logger only on first method call."""
    setup_logging(log_dir=log_dir, level="INFO", json_logs=True)
    logger = get_logger("core", "TestModule")
    assert logger._logger is None  # Ещё не создан
    logger.info("test message")
    assert logger._logger is not None  # Теперь создан


def test_lazy_logger_wraps_methods(log_dir, capsys):
    """LazyLogger should forward method calls to real logger."""
    setup_logging(log_dir=log_dir, level="INFO", json_logs=True)
    logger = get_logger("core", "TestModule")
    logger.info("test message")

    # Проверяем, что лог записан в файл
    log_file = log_dir / "core.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "test message" in content


def test_lazy_logger_repr():
    """LazyLogger __repr__ should show category and name."""
    logger = get_logger("core", "TestModule")
    assert "LazyLogger" in repr(logger)
    assert "core" in repr(logger)
    assert "TestModule" in repr(logger)


def test_alert_level_processor_auto_detect():
    """alert_level_processor should auto-detect levels."""
    event = {"level": "info", "event": "system started"}
    result = alert_level_processor(None, None, event)
    assert result["alert_level"] == "STATE"

    event = {"level": "info", "event": "user request received"}
    result = alert_level_processor(None, None, event)
    assert result["alert_level"] == "REQUEST"

    event = {"level": "warning", "event": "memory low"}
    result = alert_level_processor(None, None, event)
    assert result["alert_level"] == "WARNING"

    event = {"level": "error", "event": "connection failed"}
    result = alert_level_processor(None, None, event)
    assert result["alert_level"] == "CRITICAL_WARNING"

    event = {"level": "critical", "event": "crash"}
    result = alert_level_processor(None, None, event)
    assert result["alert_level"] == "CRASH"


def test_alert_level_processor_manual_override():
    """alert_level_processor should respect manual override."""
    event = {"level": "info", "alert_level": "REQUEST"}
    result = alert_level_processor(None, None, event)
    assert result["alert_level"] == "REQUEST"


def test_sanitize_processor_redacts_critical_keys():
    """sanitize_processor should redact critical keys."""
    from core.config import Config

    with patch.object(Config, "load", return_value=Config(sanitize_logs=True)):
        reset_sanitize_config_cache()
        event = {"level": "info", "password": "secret123", "token": "abc456"}
        result = sanitize_processor(None, None, event)
        assert result["password"] == "[REDACTED]"
        assert result["token"] == "[REDACTED]"


def test_sanitize_processor_truncates_long_fields():
    """sanitize_processor should truncate long sensitive fields."""
    from core.config import Config

    with patch.object(
        Config, "load", return_value=Config(sanitize_logs=True, max_log_field_length=10)
    ):
        reset_sanitize_config_cache()
        event = {"level": "info", "prompt": "x" * 100}
        result = sanitize_processor(None, None, event)
        assert len(result["prompt"]) == 13  # 10 + "..."
        assert result["prompt"].endswith("...")


def test_sanitize_processor_error_level_masks():
    """sanitize_processor should mask sensitive fields on ERROR level."""
    from core.config import Config

    with patch.object(Config, "load", return_value=Config(sanitize_logs=True)):
        reset_sanitize_config_cache()
        long_url = "https://example.com/" + "x" * 200
        event = {"level": "error", "url": long_url}
        result = sanitize_processor(None, None, event)
        masked = result["url"]
        assert isinstance(masked, dict)
        assert "length" in masked
        assert "hash" in masked


def test_sanitize_processor_disabled():
    """sanitize_processor should do nothing when disabled."""
    from core.config import Config

    with patch.object(Config, "load", return_value=Config(sanitize_logs=False)):
        reset_sanitize_config_cache()
        event = {"level": "info", "password": "secret123"}
        result = sanitize_processor(None, None, event)
        assert result["password"] == "secret123"


def test_reset_sanitize_config_cache():
    """reset_sanitize_config_cache should clear the cache."""
    from core.config import Config
    from core.logging_config import get_sanitize_config

    with patch.object(Config, "load", return_value=Config(sanitize_logs=True)):
        reset_sanitize_config_cache()
        config1 = get_sanitize_config()

    with patch.object(Config, "load", return_value=Config(sanitize_logs=False)):
        reset_sanitize_config_cache()
        config2 = get_sanitize_config()

    assert config1.sanitize_logs is True
    assert config2.sanitize_logs is False
