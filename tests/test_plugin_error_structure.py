"""Tests for PluginErrorResult structure (T3-017.5 Step 0)."""

import pytest

from core.plugin_types import ERROR_TYPES, is_plugin_error, make_error


def test_make_error_has_all_required_fields():
    err = make_error(
        "connection_failed",
        "Cannot connect to SearXNG",
        "Search backend unavailable. Inform the user and suggest trying later.",
    )
    assert err["status"] == "error"
    assert err["error_type"] == "connection_failed"
    assert err["message"]
    assert err["retry_advice"]


def test_make_error_rejects_unknown_type():
    with pytest.raises(ValueError):
        make_error("weird_type", "message", "advice")


def test_make_error_optional_http_status():
    err = make_error("http_error", "HTTP 403", "Do not retry the same URL.", http_status=403)
    assert err["http_status"] == 403


def test_is_plugin_error_detection():
    err = make_error("timeout", "timed out", "Retry once with smaller limit.")
    assert is_plugin_error(err)
    assert not is_plugin_error({"status": "success", "source": "mock"})
    assert not is_plugin_error({"status": "error", "message": "old style"})
    assert not is_plugin_error("string")
    assert not is_plugin_error(None)


def test_all_error_types_covered():
    assert set(ERROR_TYPES) == {
        "backend_unavailable",
        "http_error",
        "connection_failed",
        "timeout",
        "rate_limit",
        "invalid_url",
    }
