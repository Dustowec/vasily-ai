"""ADR-014 tests: consecutive-error dush and max_iterations final word.

All tests exercise ReActLoop.run() end-to-end with a fake LLM and fake
plugin registry. This is the (a)+(b) hybrid from the design:
- counter logic is observed through what ReActLoop sends back to the LLM,
- integration is observed through the final ReActResult.

No real network, no real Ollama, no real plugins.
"""

from types import SimpleNamespace

import pytest

from core.react_loop import (
    CONSECUTIVE_ERROR_DUSH_MESSAGE,
    MAX_ITERATIONS_FINAL_USER_MESSAGE,
    ReActLoop,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeTokenManager:
    """No-op token manager: no trimming, fixed usage report."""

    def __init__(self, *args, **kwargs):
        pass

    def trim_messages(self, messages):
        return messages

    def get_usage_report(self, messages):
        return {
            "used_tokens": 100,
            "max_tokens": 8192,
            "usage_percent": 1.2,
            "available_tokens": 8000,
        }


class FakePromptsLibrary:
    """Returns a constant prompt regardless of type."""

    def __init__(self, *args, **kwargs):
        pass

    def get_prompt(self, name):
        return "test system prompt"


class FakeLLM:
    """Returns pre-queued responses. Records what it received.

    When called with tools=[] (the max_iterations final word), returns a
    fixed text answer instead of popping from the queue — this mimics
    "model gives final answer without tools".
    """

    def __init__(self, responses, final_content="final answer"):
        self.responses = list(responses)
        self.final_content = final_content
        self.received_messages = []
        self.received_tools = []
        self.token_manager = None

    async def chat(self, messages, tools=None):
        self.received_messages.append([dict(m) for m in messages])
        self.received_tools.append(list(tools) if tools is not None else None)
        if tools == []:
            return {"message": {"content": self.final_content, "tool_calls": []}}
        if not self.responses:
            return {"message": {"content": "no more responses", "tool_calls": []}}
        return self.responses.pop(0)


class FakePlugin:
    def __init__(self, name, result):
        self.name = name
        self._result = result

    async def execute(self, **kwargs):
        return self._result


class FakePluginRegistry:
    """Registry with N tools, all returning the same fixed result."""

    def __init__(self, results_by_tool):
        self._plugins = {name: FakePlugin(name, result) for name, result in results_by_tool.items()}

    def get(self, name):
        return self._plugins.get(name)

    def get_tools_schema(self):
        return [
            {
                "name": name,
                "description": f"fake {name}",
                "parameters": {
                    "url": {"type": "string", "required": True},
                    "query": {"type": "string", "required": False},
                    "path": {"type": "string", "required": False},
                    "message": {"type": "string", "required": False},
                    "limit": {"type": "integer", "required": False},
                },
            }
            for name in self._plugins
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def tool_call(name, n):
    """Build an LLM response with one tool_call and distinct args."""
    return {
        "message": {
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": name,
                        "arguments": {"url": f"http://example.com/{n}"},
                    }
                }
            ],
        }
    }


def final_response():
    """LLM response with no tool_calls — loop should finish."""
    return {"message": {"content": "done", "tool_calls": []}}


def count_dush_in_last_snapshot(llm):
    """How many times does the dush message appear in the last snapshot?"""
    if not llm.received_messages:
        return 0
    last = llm.received_messages[-1]
    return sum(1 for m in last if m.get("content") == CONSECUTIVE_ERROR_DUSH_MESSAGE)


def any_snapshot_has_dush(llm):
    for snapshot in llm.received_messages:
        for m in snapshot:
            if m.get("content") == CONSECUTIVE_ERROR_DUSH_MESSAGE:
                return True
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def make_loop(monkeypatch):
    """Factory: build a ReActLoop with all heavy deps faked out."""

    def _make(responses, plugin_results, max_iterations=7):
        monkeypatch.setattr("core.react_loop.TokenManager", FakeTokenManager)
        monkeypatch.setattr("core.react_loop.GoldenPromptsLibrary", FakePromptsLibrary)
        monkeypatch.setattr(
            "core.react_loop.OllamaClient.extract_thinking_and_answer",
            staticmethod(lambda c: ("", c)),
        )

        cfg = SimpleNamespace(
            max_react_iterations=max_iterations,
            max_tool_calls_per_tool=10,
            log_preview_length=50,
            plugin_timeout=5.0,
            request_timeout=60.0,
            llm_num_ctx=8192,
            llm_safety_margin=0.1,
            max_tool_content_chars=4000,
            dev_mode=False,
        )

        llm = FakeLLM(responses)
        registry = FakePluginRegistry(plugin_results)
        loop = ReActLoop(cfg, llm, registry)
        return loop, llm

    return _make


# ---------------------------------------------------------------------------
# Dush: threshold behaviour
# ---------------------------------------------------------------------------


async def test_dush_not_injected_after_one_error(make_loop):
    responses = [tool_call("web_scraper", 1), final_response()]
    error = {"status": "error", "error_type": "invalid_url", "message": "blocked"}
    loop, llm = make_loop(responses, {"web_scraper": error})

    await loop.run("test")

    assert not any_snapshot_has_dush(llm), "Dush injected too early (1 error)"


async def test_dush_not_injected_after_two_errors(make_loop):
    responses = [
        tool_call("web_scraper", 1),
        tool_call("web_scraper", 2),
        final_response(),
    ]
    error = {"status": "error", "error_type": "invalid_url", "message": "blocked"}
    loop, llm = make_loop(responses, {"web_scraper": error})

    await loop.run("test")

    assert not any_snapshot_has_dush(llm), "Dush injected too early (2 errors)"


async def test_dush_injected_exactly_at_third_error(make_loop):
    responses = [
        tool_call("web_scraper", 1),
        tool_call("web_scraper", 2),
        tool_call("web_scraper", 3),
        final_response(),
    ]
    error = {"status": "error", "error_type": "invalid_url", "message": "blocked"}
    loop, llm = make_loop(responses, {"web_scraper": error})

    await loop.run("test")

    assert any_snapshot_has_dush(llm), "Dush NOT injected at 3rd error"
    assert count_dush_in_last_snapshot(llm) == 1, "Dush should appear once"


async def test_dush_not_repeated_beyond_threshold(make_loop):
    # 5 consecutive errors — dush must appear exactly once.
    responses = [tool_call("web_scraper", n) for n in range(1, 6)] + [final_response()]
    error = {"status": "error", "error_type": "invalid_url", "message": "blocked"}
    loop, llm = make_loop(responses, {"web_scraper": error})

    await loop.run("test")

    assert (
        count_dush_in_last_snapshot(llm) == 1
    ), f"Dush repeated: appeared {count_dush_in_last_snapshot(llm)} times"


# ---------------------------------------------------------------------------
# Dush: counter reset semantics
# ---------------------------------------------------------------------------


async def test_counter_resets_after_success(make_loop):
    # 2 errors → success → 2 errors. Dush must NOT fire.
    responses = [
        tool_call("web_scraper", 1),
        tool_call("web_scraper", 2),
        tool_call("web_scraper", 3),  # this one will succeed
        tool_call("web_scraper", 4),
        tool_call("web_scraper", 5),
        final_response(),
    ]

    call_count = {"n": 0}

    class SequentialPlugin:
        def __init__(self):
            self.name = "web_scraper"

        async def execute(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 3:
                return {"status": "success", "source": "web", "content": "ok"}
            return {"status": "error", "error_type": "invalid_url", "message": "blocked"}

    class SequentialRegistry:
        def get(self, name):
            return SequentialPlugin() if name == "web_scraper" else None

        def get_tools_schema(self):
            return [
                {
                    "name": "web_scraper",
                    "description": "",
                    "parameters": {"url": {"type": "string", "required": True}},
                }
            ]

    # Build loop manually (not via fixture) to use sequential plugin.
    loop, llm = make_loop(responses, {"web_scraper": {"status": "error"}})
    # Swap the registry in-place.
    loop.plugin_registry = SequentialRegistry()
    loop._tool_param_names = {"web_scraper": {"url"}}
    loop.tools = loop._build_tools()

    await loop.run("test")

    assert not any_snapshot_has_dush(llm), "Dush fired despite counter reset after success"


async def test_counter_is_per_tool(make_loop):
    # 2 errors on tool_A + 2 errors on tool_B (interleaved).
    # Neither tool reaches 3 consecutive — no dush.
    responses = [
        tool_call("web_scraper", 1),
        tool_call("web_search", 2),
        tool_call("web_scraper", 3),
        tool_call("web_search", 4),
        final_response(),
    ]
    error = {"status": "error", "message": "blocked"}
    loop, llm = make_loop(responses, {"web_scraper": error, "web_search": error})

    await loop.run("test")

    assert not any_snapshot_has_dush(llm), "Dush fired across tools (counter must be per-tool)"


# ---------------------------------------------------------------------------
# max_iterations: final word
# ---------------------------------------------------------------------------


async def test_max_iterations_returns_status_failed(make_loop):
    # Every LLM call returns a successful tool_call; loop exhausts iterations.
    responses = [tool_call("web_scraper", n) for n in range(1, 8)]
    success = {"status": "success", "source": "web", "content": "ok"}
    loop, llm = make_loop(responses, {"web_scraper": success}, max_iterations=7)

    result = await loop.run("test")

    assert result["status"] == "failed"
    assert result["iterations"] == 7
    assert result["answer"] == "final answer"


async def test_max_iterations_final_call_uses_empty_tools(make_loop):
    responses = [tool_call("web_scraper", n) for n in range(1, 8)]
    success = {"status": "success", "source": "web", "content": "ok"}
    loop, llm = make_loop(responses, {"web_scraper": success}, max_iterations=7)

    await loop.run("test")

    # Last call must be with tools=[] (final word, no more tool calling).
    assert (
        llm.received_tools[-1] == []
    ), f"Final call sent tools={llm.received_tools[-1]!r}, expected []"


async def test_max_iterations_final_user_message_injected(make_loop):
    responses = [tool_call("web_scraper", n) for n in range(1, 8)]
    success = {"status": "success", "source": "web", "content": "ok"}
    loop, llm = make_loop(responses, {"web_scraper": success}, max_iterations=7)

    await loop.run("test")

    last_snapshot = llm.received_messages[-1]
    assert any(
        m.get("content") == MAX_ITERATIONS_FINAL_USER_MESSAGE for m in last_snapshot
    ), "Final user nudge not found in last LLM call"


async def test_max_iterations_falls_back_on_empty_final_answer(monkeypatch, make_loop):
    responses = [tool_call("web_scraper", n) for n in range(1, 8)]
    success = {"status": "success", "source": "web", "content": "ok"}
    loop, llm = make_loop(responses, {"web_scraper": success}, max_iterations=7)
    # Force the final answer to be empty.
    llm.final_content = ""

    result = await loop.run("test")

    assert result["status"] == "failed"
    # Fallback: ReActLoop should return _last_assistant_content, not "".
    assert result["answer"] != ""
