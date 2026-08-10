"""Tests for ReAct TypedDict structures (P3-3)."""

from core.react_types import ReActResult, ReActStep, TokenUsage


def test_token_usage_structure():
    """TokenUsage has all required fields."""
    usage: TokenUsage = {
        "used_tokens": 500,
        "max_tokens": 32768,
        "usage_percent": 1.5,
        "available_tokens": 32268,
    }
    assert usage["used_tokens"] == 500
    assert usage["max_tokens"] == 32768
    assert usage["usage_percent"] == 1.5
    assert usage["available_tokens"] == 32268


def test_react_step_structure():
    """ReActStep has all required fields."""
    step: ReActStep = {
        "iteration": 1,
        "tool": "web_search",
        "args": {"query": "test", "limit": 5},
        "result_preview": "Found 5 results...",
    }
    assert step["iteration"] == 1
    assert step["tool"] == "web_search"
    assert step["args"]["query"] == "test"
    assert step["result_preview"] == "Found 5 results..."


def test_react_result_structure():
    """ReActResult has all required fields."""
    result: ReActResult = {
        "status": "success",
        "answer": "Here is the answer.",
        "iterations": 2,
        "steps": [
            {
                "iteration": 1,
                "tool": "echo",
                "args": {"message": "test"},
                "result_preview": "echoed",
            }
        ],
        "token_usage": {
            "used_tokens": 200,
            "max_tokens": 32768,
            "usage_percent": 0.6,
            "available_tokens": 32568,
        },
    }
    assert result["status"] == "success"
    assert result["iterations"] == 2
    assert len(result["steps"]) == 1
    assert result["token_usage"]["used_tokens"] == 200
