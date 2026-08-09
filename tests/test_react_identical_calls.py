"""Tests: deduplication of identical tool calls (T3-017.5 Step 2)."""

from core.config import Config
from core.react_loop import ReActLoop


class CountingEcho:
    def __init__(self):
        self.executions = 0

    async def execute(self, **kwargs):
        self.executions += 1
        return {"status": "success", "echo": kwargs.get("message", "")}


class FakeRegistry:
    def __init__(self, tool):
        self._tool = tool

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
            }
        ]

    def get(self, name):
        return self._tool if name == "echo" else None


class FakeLLMRepeat:
    """Requests the same echo call 4 times, then gives a final answer."""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls <= 4:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "echo",
                                "arguments": {"message": "same"},
                            }
                        }
                    ],
                }
            }
        return {"message": {"content": "done"}}


class FakeLLMVarying:
    """Requests echo with different args twice, then final answer."""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls <= 2:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "echo",
                                "arguments": {"message": f"variant-{self.calls}"},
                            }
                        }
                    ],
                }
            }
        return {"message": {"content": "done"}}


async def test_identical_calls_dedup_and_limit():
    """1st call executes, 2nd is DUPLICATE, 3rd+ are LIMIT REACHED."""
    config = Config.load()
    tool = CountingEcho()
    loop = ReActLoop(config=config, llm_client=FakeLLMRepeat(), plugin_registry=FakeRegistry(tool))
    result = await loop.run("keep calling echo")

    assert tool.executions == 1
    assert len(result["steps"]) == 4
    previews = [step["result_preview"] for step in result["steps"]]
    assert "success" in previews[0]
    assert "DUPLICATE CALL" in previews[1]
    assert "LIMIT REACHED" in previews[2]
    assert "LIMIT REACHED" in previews[3]
    assert result["status"] == "success"


async def test_different_args_not_deduped():
    """Different arguments are executed normally, no dedup."""
    config = Config.load()
    tool = CountingEcho()
    loop = ReActLoop(
        config=config,
        llm_client=FakeLLMVarying(),
        plugin_registry=FakeRegistry(tool),
    )
    result = await loop.run("call echo with different args")

    assert tool.executions == 2
    assert result["status"] == "success"
