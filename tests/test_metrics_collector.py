"""Tests for TZ-023: MetricsCollector."""

from core.metrics import MetricsCollector


def test_initial_snapshot_is_zero():
    """New collector must return zeroed metrics."""
    metrics = MetricsCollector()
    snapshot = metrics.snapshot()

    assert snapshot["requests_total"] == 0
    assert snapshot["requests_success"] == 0
    assert snapshot["requests_error"] == 0
    assert snapshot["requests_interrupted"] == 0
    assert snapshot["avg_duration_ms"] == 0.0
    assert snapshot["iterations_total"] == 0
    assert snapshot["tool_calls_total"] == 0
    assert snapshot["tool_calls_blocked"] == 0
    assert snapshot["tokens_used_total"] == 0


def test_record_request_updates_counters():
    """Request counters and average duration must be updated."""
    metrics = MetricsCollector()

    metrics.record_request(duration_ms=100.0, status="success", iterations=2)
    metrics.record_request(duration_ms=300.0, status="error", iterations=1)

    snapshot = metrics.snapshot()

    assert snapshot["requests_total"] == 2
    assert snapshot["requests_success"] == 1
    assert snapshot["requests_error"] == 1
    assert snapshot["requests_interrupted"] == 0
    assert snapshot["avg_duration_ms"] == 200.0
    assert snapshot["iterations_total"] == 3


def test_record_request_handles_interrupted_status():
    """Interrupted requests must be counted separately."""
    metrics = MetricsCollector()

    metrics.record_request(duration_ms=50.0, status="interrupted", iterations=1)

    snapshot = metrics.snapshot()

    assert snapshot["requests_total"] == 1
    assert snapshot["requests_interrupted"] == 1
    assert snapshot["requests_success"] == 0
    assert snapshot["requests_error"] == 0


def test_record_react_result_counts_tools_and_tokens():
    """ReAct result must add tool-call and token metrics."""
    metrics = MetricsCollector()

    result = {
        "status": "success",
        "answer": "done",
        "iterations": 2,
        "steps": [
            {
                "iteration": 1,
                "tool": "echo",
                "args": {"message": "hi"},
                "result_preview": "success",
            },
            {
                "iteration": 2,
                "tool": "echo",
                "args": {"message": "hi"},
                "result_preview": "[DUPLICATE CALL] same result",
            },
        ],
        "token_usage": {
            "used_tokens": 120,
            "max_tokens": 32768,
            "usage_percent": 0.4,
            "available_tokens": 32648,
        },
    }

    metrics.record_react_result(result)

    snapshot = metrics.snapshot()

    assert snapshot["tool_calls_total"] == 2
    assert snapshot["tool_calls_blocked"] == 1
    assert snapshot["tokens_used_total"] == 120
