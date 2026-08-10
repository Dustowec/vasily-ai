"""Tests for TZ-024: service auto-start (Ollama launcher)."""

from unittest.mock import AsyncMock, MagicMock, patch

from core.service_launcher import ensure_ollama_running


async def test_returns_true_when_ollama_already_running():
    """If Ollama responds to health check, return True immediately."""
    config = MagicMock()
    config.llm_url = "http://localhost:11434"
    config.llm_auto_start = True
    config.llm_auto_start_timeout = 10

    with patch("core.service_launcher._ollama_health", new_callable=AsyncMock) as mock_health:
        mock_health.return_value = True
        result = await ensure_ollama_running(config)

    assert result is True
    mock_health.assert_called_once_with(config.llm_url)


async def test_returns_false_when_unavailable_and_auto_start_disabled():
    """If Ollama is down and llm_auto_start=False, return False."""
    config = MagicMock()
    config.llm_url = "http://localhost:11434"
    config.llm_auto_start = False
    config.llm_auto_start_timeout = 10

    with patch("core.service_launcher._ollama_health", new_callable=AsyncMock) as mock_health:
        mock_health.return_value = False
        result = await ensure_ollama_running(config)

    assert result is False


async def test_starts_ollama_when_auto_start_enabled():
    """If Ollama is down and llm_auto_start=True, launch it and wait."""
    config = MagicMock()
    config.llm_url = "http://localhost:11434"
    config.llm_auto_start = True
    config.llm_auto_start_timeout = 5

    call_count = {"n": 0}

    async def fake_health(url):
        call_count["n"] += 1
        return call_count["n"] >= 3

    with (
        patch("core.service_launcher._ollama_health", side_effect=fake_health),
        patch("core.service_launcher._launch_ollama_process") as mock_launch,
    ):
        result = await ensure_ollama_running(config)

    assert result is True
    mock_launch.assert_called_once()


async def test_returns_false_after_timeout():
    """If Ollama never starts within timeout, return False."""
    config = MagicMock()
    config.llm_url = "http://localhost:11434"
    config.llm_auto_start = True
    config.llm_auto_start_timeout = 0.2

    with (
        patch("core.service_launcher._ollama_health", new_callable=AsyncMock, return_value=False),
        patch("core.service_launcher._launch_ollama_process") as mock_launch,
    ):
        result = await ensure_ollama_running(config)

    assert result is False
    mock_launch.assert_called_once()
