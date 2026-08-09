"""Tests for dev_mode mock policy (Sprint 1, Step 3)."""

from core.config import Config
from core.react_loop import ReActLoop


class MockTool:
    async def execute(self, **kwargs):
        return {"status": "success", "source": "mock", "data": "fake"}


class FakeRegistry:
    def get_tools_schema(self):
        return [
            {
                "name": "mocky",
                "description": "Returns mock data",
                "parameters": {"q": {"type": "string", "description": "q", "required": True}},
            }
        ]

    def get(self, name):
        return MockTool() if name == "mocky" else None


class FakeLLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "mocky", "arguments": {"q": "x"}}}],
                }
            }
        return {"message": {"content": "done"}}


async def test_mock_blocked_outside_dev_mode():
    """Outside dev_mode the LLM must see an error, not fake success."""
    config = Config.load()
    assert config.dev_mode is False
    loop = ReActLoop(config=config, llm_client=FakeLLM(), plugin_registry=FakeRegistry())
    result = await loop.run("use mocky")
    assert result["steps"]
    assert all("error" in step["result_preview"] for step in result["steps"])


async def test_mock_allowed_in_dev_mode():
    """In dev_mode mock data passes through for local experiments."""
    config = Config.load()
    config.dev_mode = True
    loop = ReActLoop(config=config, llm_client=FakeLLM(), plugin_registry=FakeRegistry())
    result = await loop.run("use mocky")
    assert result["steps"]
    assert all("success" in step["result_preview"] for step in result["steps"])
