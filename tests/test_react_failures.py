"""Failure tests for ReAct loop (Sprint 1, Step 0)."""

import json

from core.config import Config
from core.react_loop import ReActLoop
from integrations.ollama_client import LLMUnavailableError


class FakeTool:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail

    async def execute(self, **kwargs):
        if self.fail:
            raise RuntimeError("plugin exploded")
        return {"status": "success", "echo": kwargs.get("message", "")}


class FakeRegistry:
    def __init__(self):
        self._tools = {
            "echo": FakeTool("echo"),
            "boom": FakeTool("boom", fail=True),
        }

    def get_tools_schema(self):
        return [
            {
                "name": "echo",
                "description": "Echo a message",
                "parameters": {
                    "message": {
                        "type": "string",
                        "description": "text",
                        "required": True,
                    }
                },
            },
            {
                "name": "boom",
                "description": "Always fails",
                "parameters": {
                    "message": {
                        "type": "string",
                        "description": "text",
                        "required": True,
                    }
                },
            },
        ]

    def get(self, name):
        return self._tools.get(name)


class FakeLLMDown:
    async def chat(self, messages, tools=None, **kwargs):
        raise LLMUnavailableError("Ollama is down")


class FakeLLMStateful:
    def __init__(self, first_response):
        self.first_response = first_response
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return self.first_response
        return {"message": {"content": "final answer"}}


def make_loop(llm):
    config = Config.load()
    return ReActLoop(config=config, llm_client=llm, plugin_registry=FakeRegistry())


async def test_react_loop_ollama_down():
    """RED now: LLMUnavailableError propagates. GREEN after P1-4."""
    loop = make_loop(FakeLLMDown())
    result = await loop.run("hello")
    assert isinstance(result, dict)
    assert result.get("status") in ("error", "llm_unavailable")


async def test_react_loop_plugin_throws():
    """GREEN now: R1 already works. Regression guard."""
    first = {
        "message": {
            "content": "",
            "tool_calls": [{"function": {"name": "boom", "arguments": {"message": "hi"}}}],
        }
    }
    loop = make_loop(FakeLLMStateful(first))
    result = await loop.run("trigger boom")
    assert result.get("status") == "success"
    assert any("error" in step.get("result_preview", "") for step in result["steps"])


async def test_react_loop_malformed_response():
    """RED now: string arguments cause TypeError. GREEN after P1-4 parsing."""
    first = {
        "message": {
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "echo",
                        "arguments": json.dumps({"message": "hi"}),
                    }
                }
            ],
        }
    }
    loop = make_loop(FakeLLMStateful(first))
    result = await loop.run("trigger echo")
    assert result.get("status") == "success"
    assert any("success" in step.get("result_preview", "") for step in result["steps"])
