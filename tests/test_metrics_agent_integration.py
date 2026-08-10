"""Integration tests: MetricsCollector connected to AgentCore."""

from unittest.mock import AsyncMock, MagicMock

from core.agent import AgentCore
from core.config import Config
from core.metrics import MetricsCollector


def test_agent_has_metrics_collector():
    """AgentCore must initialize MetricsCollector."""
    config = Config.load()
    agent = AgentCore(config)

    assert hasattr(agent, "metrics")
    assert isinstance(agent.metrics, MetricsCollector)


async def test_handle_request_records_metrics():
    """handle_request must record request metrics."""
    config = Config.load()
    agent = AgentCore(config)

    # Mock react_loop to avoid real LLM calls
    mock_loop = MagicMock()
    mock_loop.run = AsyncMock(
        return_value={
            "status": "success",
            "answer": "test answer",
            "iterations": 2,
            "steps": [
                {
                    "iteration": 1,
                    "tool": "echo",
                    "args": {"message": "hi"},
                    "result_preview": "success",
                }
            ],
            "token_usage": {
                "used_tokens": 100,
                "max_tokens": 32768,
                "usage_percent": 0.3,
                "available_tokens": 32668,
            },
        }
    )
    agent.react_loop = mock_loop

    # Initial state
    snapshot_before = agent.metrics.snapshot()
    assert snapshot_before["requests_total"] == 0

    # Process request
    await agent.handle_request({"text": "test query"})

    # Check metrics updated
    snapshot_after = agent.metrics.snapshot()
    assert snapshot_after["requests_total"] == 1
    assert snapshot_after["requests_success"] == 1
    assert snapshot_after["iterations_total"] == 2
    assert snapshot_after["tool_calls_total"] == 1
    assert snapshot_after["tokens_used_total"] == 100
    assert snapshot_after["avg_duration_ms"] > 0


async def test_get_metrics_includes_collector_snapshot():
    """get_metrics() must include MetricsCollector data."""
    config = Config.load()
    agent = AgentCore(config)

    # Add some metrics
    agent.metrics.record_request(
        duration_ms=150.0,
        status="success",
        iterations=1,
    )

    result = agent.get_metrics()

    # Check that extended metrics are included
    assert "requests_total" in result
    assert result["requests_total"] == 1
    assert "tool_calls_total" in result
    assert "tokens_used_total" in result
