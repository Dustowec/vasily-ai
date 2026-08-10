"""TokenManager pair-safe trimming tests (Coverage Hardening, file 3 of 3)."""

import threading

from core.token_manager import TokenManager


def _assistant_with_tool_call(name="echo"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": {"message": "x"}}}],
    }


def test_no_trim_under_limit():
    tm = TokenManager(max_tokens=1000, safety_margin=0)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    result = tm.trim_messages(messages)
    assert result == messages


def test_trim_removes_middle_groups_keeping_first_and_last():
    tm = TokenManager(max_tokens=200, safety_margin=0)
    filler = "x" * 400
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "first request"},
        {"role": "assistant", "content": filler},
        {"role": "user", "content": "middle request"},
        {"role": "assistant", "content": filler},
        {"role": "user", "content": "last request"},
        {"role": "assistant", "content": "short final"},
    ]
    result = tm.trim_messages(messages)
    contents = [m["content"] for m in result]
    assert "system prompt" in contents
    assert "first request" in contents
    assert "last request" in contents
    assert "middle request" not in contents


def test_system_message_always_preserved():
    tm = TokenManager(max_tokens=100, safety_margin=0)
    filler = "y" * 400
    messages = [
        {"role": "system", "content": "important system rules"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": filler},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": filler},
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
    ]
    result = tm.trim_messages(messages)
    assert result[0]["role"] == "system"
    assert result[0]["content"] == "important system rules"


def test_pairs_never_split():
    """assistant(tool_calls) must always be followed by its tool message."""
    tm = TokenManager(max_tokens=160, safety_margin=0)
    filler = "z" * 400
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "turn one"},
        _assistant_with_tool_call(),
        {"role": "tool", "content": filler},
        {"role": "assistant", "content": "answer one"},
        {"role": "user", "content": "turn two"},
        _assistant_with_tool_call(),
        {"role": "tool", "content": filler},
        {"role": "assistant", "content": "answer two"},
    ]
    result = tm.trim_messages(messages)
    for i, msg in enumerate(result):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            assert i + 1 < len(result), "tool_calls without following tool message"
            assert result[i + 1]["role"] == "tool"


def test_single_turn_truncates_tool_payload():
    tm = TokenManager(max_tokens=120, safety_margin=0)
    huge = "w" * 4000
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "search for data"},
        _assistant_with_tool_call("web_search"),
        {"role": "tool", "content": huge},
        {"role": "assistant", "content": "done"},
    ]
    result = tm.trim_messages(messages)
    tool_msg = next(m for m in result if m["role"] == "tool")
    assert len(tool_msg["content"]) <= 200
    assert tool_msg["content"].endswith("...")
    idx = result.index(tool_msg)
    assert result[idx - 1].get("tool_calls")


def test_truncation_terminates_when_budget_still_exceeded():
    """Regression guard: the truncation loop must always terminate.

    Buggy version truncated to [:200] + '...' (203 chars) and never reached
    the len <= 200 stop condition, looping forever. A daemon thread makes
    the regression fail fast instead of hanging the test run.
    """
    tm = TokenManager(max_tokens=50, safety_margin=0)
    messages = [
        {"role": "user", "content": "fetch the document"},
        _assistant_with_tool_call(),
        {"role": "tool", "content": "q" * 8000},
        {"role": "assistant", "content": "here you go"},
    ]
    holder = {}

    def run():
        holder["result"] = tm.trim_messages(messages)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=3)

    assert not thread.is_alive(), "trim_messages hung in truncation loop"
    tool_msg = next(m for m in holder["result"] if m["role"] == "tool")
    assert len(tool_msg["content"]) <= 200


def test_safety_margin_reduces_budget():
    tm = TokenManager(max_tokens=200, safety_margin=150)
    filler = "x" * 200
    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": filler},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": filler},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "end"},
    ]
    result = tm.trim_messages(messages)
    contents = [m["content"] for m in result]
    assert "one" in contents
    assert "three" in contents
    assert "two" not in contents


def test_estimate_tokens_cyrillic_heavier_than_latin():
    tm = TokenManager(max_tokens=1000)
    assert tm.estimate_tokens("a" * 100) == 26
    assert tm.estimate_tokens("а" * 100) == 41
