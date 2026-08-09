"""Tests for log sanitization processor (T3-016.5, P3-1)."""

import pytest

from core.config import Config
from core.logging_config import sanitize_processor


@pytest.fixture(autouse=True)
def default_config(monkeypatch):
    """Force default config regardless of vasily_config.json or env."""
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: Config()))


def test_info_truncates_sensitive_fields():
    event = {
        "level": "info",
        "event": "Test event",
        "prompt": "x" * 200,
        "query": "short",
    }
    result = sanitize_processor(None, "info", event)
    assert result["prompt"] == "x" * 100 + "..."
    assert result["query"] == "short"


def test_error_masks_sensitive_fields_with_metadata():
    long_url = "https://example.com/" + "x" * 200
    event = {"level": "error", "event": "Test error", "url": long_url}
    result = sanitize_processor(None, "error", event)
    masked = result["url"]
    assert isinstance(masked, dict)
    assert "example.com" not in str(masked)
    assert masked["length"] == len(long_url)
    assert len(masked["hash"]) == 8


def test_critical_keys_redacted_completely():
    event = {
        "level": "info",
        "event": "Test",
        "password": "secret123",
        "token": "abc456",
    }
    result = sanitize_processor(None, "info", event)
    assert result["password"] == "[REDACTED]"
    assert result["token"] == "[REDACTED]"


def test_non_sensitive_fields_unchanged():
    event = {"level": "info", "event": "Test", "user_id": 12345, "status": "ok"}
    result = sanitize_processor(None, "info", event)
    assert result["user_id"] == 12345
    assert result["status"] == "ok"


def test_sanitization_disabled_keeps_full_data(monkeypatch):
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: Config(sanitize_logs=False)))
    long_text = "sensitive " * 50
    event = {"level": "info", "event": "Test", "text": long_text}
    result = sanitize_processor(None, "info", event)
    assert result["text"] == long_text
