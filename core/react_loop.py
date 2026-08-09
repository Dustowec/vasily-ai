"""ReAct loop - Reasoning + Acting pattern (T3-016).

Mandatory requirements implemented:
- R1: Plugin errors caught and returned to LLM history, cycle continues.
- R2: Tool call limit (3 per tool per session), force stop on exceed.
- R3: KeyboardInterrupt handled, returns partial progress.
- R4: Step logging across core/llm/interaction journals with request_id.
- Token management: context window control (T3-018).
- Golden Prompts: curated system prompts per task type (T3-020).
"""

import asyncio
import json
from typing import Any

from core.golden_prompts import GoldenPromptsLibrary
from core.logging_config import get_logger
from core.token_manager import TokenManager

core_logger = get_logger("core", "ReActLoop")
llm_logger = get_logger("llm", "ReActLoop")
interaction_logger = get_logger("interaction", "ReActLoop")

DEFAULT_SYSTEM_PROMPT = (
    "You are Vasily, a helpful AI agent. You can use tools to accomplish tasks. "
    "Think step by step. If a tool is needed, call it. "
    "When you have the final answer, respond directly without tool calls. "
    "If a tool returns an error, consider another approach or explain the failure."
)

MAX_TOOL_CALLS_PER_TOOL = 3
LOG_PREVIEW_LENGTH = 100


class ReActLoop:
    """Executes Reasoning + Acting cycle with the LLM and plugins."""

    def __init__(self, config, llm_client, plugin_registry):
        self.config = config
        self.llm = llm_client
        self.plugin_registry = plugin_registry
        self.max_iterations = config.max_react_iterations
        self.tools = self._build_tools()

        # Token manager with num_ctx from config
        self.token_manager = TokenManager(config.llm_num_ctx)

        # Golden prompts library
        self.prompts_library = GoldenPromptsLibrary()

    def _build_tools(self) -> list[dict[str, Any]]:
        """Convert plugin schemas to Ollama tool format."""
        tools = []
        for schema in self.plugin_registry.get_tools_schema():
            properties = {}
            required = []
            for param_name, param_def in schema.get("parameters", {}).items():
                properties[param_name] = {
                    "type": param_def.get("type", "string"),
                    "description": param_def.get("description", ""),
                }
                if param_def.get("required"):
                    required.append(param_name)

            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": schema["name"],
                        "description": schema["description"],
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
            )
        return tools

    async def run(self, user_request: str, prompt_type: str = "default") -> dict[str, Any]:
        """Run the ReAct cycle for a user request."""
        system_prompt = self.prompts_library.get_prompt(prompt_type) or DEFAULT_SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_request},
        ]
        tool_call_counts: dict[str, int] = {}
        steps: list[dict[str, Any]] = []

        core_logger.info(
            "ReAct loop started",
            max_iterations=self.max_iterations,
            prompt_type=prompt_type,
            request_preview=user_request[:LOG_PREVIEW_LENGTH],
        )

        for iteration in range(self.max_iterations):
            core_logger.info("ReAct iteration started", iteration=iteration + 1)

            # Trim messages if context overflow
            messages = self.token_manager.trim_messages(messages)

            # Log token usage
            usage = self.token_manager.get_usage_report(messages)
            core_logger.info(
                "Token usage",
                iteration=iteration + 1,
                used_tokens=usage["used_tokens"],
                usage_percent=usage["usage_percent"],
            )

            # R3: handle user interruption
            try:
                response = await self.llm.chat(messages=messages, tools=self.tools)
            except (KeyboardInterrupt, asyncio.CancelledError):
                core_logger.warning("ReAct interrupted by user", iteration=iteration + 1)
                return {
                    "status": "interrupted",
                    "answer": self._last_assistant_content(messages),
                    "iterations": iteration + 1,
                    "steps": steps,
                    "token_usage": usage,
                }

            llm_logger.info(
                "LLM response received",
                iteration=iteration + 1,
                has_tool_calls=bool(response.get("message", {}).get("tool_calls")),
            )

            message = response.get("message", {})
            tool_calls = message.get("tool_calls") or []
            content = message.get("content", "")

            messages.append({"role": "assistant", "content": content})

            # No tool calls -> final answer
            if not tool_calls:
                core_logger.info("ReAct loop finished", iterations=iteration + 1)
                return {
                    "status": "success",
                    "answer": content,
                    "iterations": iteration + 1,
                    "steps": steps,
                    "token_usage": usage,
                }

            # Process tool calls
            for tool_call in tool_calls:
                function = tool_call.get("function", {})
                tool_name = function.get("name", "")
                args = function.get("arguments", {}) or {}

                # R2: enforce per-tool call limit
                if tool_call_counts.get(tool_name, 0) >= MAX_TOOL_CALLS_PER_TOOL:
                    core_logger.warning("Tool call limit exceeded", tool=tool_name)
                    messages.append(
                        {
                            "role": "tool",
                            "content": (
                                f"Error: call limit exceeded for tool "
                                f"'{tool_name}'. Use another tool or give "
                                f"the final answer."
                            ),
                        }
                    )
                    continue

                tool_call_counts[tool_name] = tool_call_counts.get(tool_name, 0) + 1

                interaction_logger.info(
                    "Calling plugin",
                    tool=tool_name,
                    args_preview=str(args)[:LOG_PREVIEW_LENGTH],
                )

                # R1: catch plugin errors, feed back to LLM
                try:
                    plugin = self.plugin_registry.get(tool_name)
                    if plugin is None:
                        raise ValueError(f"Plugin not found: {tool_name}")
                    result = await plugin.execute(**args)

                    # P1-3: block mock data outside dev_mode
                    if (
                        isinstance(result, dict)
                        and result.get("source") == "mock"
                        and not self.config.dev_mode
                    ):
                        tool_content = json.dumps(
                            {
                                "status": "error",
                                "error": (
                                    f"Tool '{tool_name}' backend is unavailable. "
                                    "Mock data is disabled outside dev_mode."
                                ),
                            },
                            ensure_ascii=False,
                        )
                        interaction_logger.warning("Mock result blocked", tool=tool_name)
                    else:
                        tool_content = json.dumps(result, ensure_ascii=False)
                    interaction_logger.info(
                        "Plugin returned result",
                        tool=tool_name,
                        result_preview=tool_content[:LOG_PREVIEW_LENGTH],
                    )
                except Exception as e:
                    interaction_logger.error("Plugin failed", tool=tool_name, error=str(e))
                    tool_content = json.dumps({"error": str(e)}, ensure_ascii=False)

                steps.append(
                    {
                        "iteration": iteration + 1,
                        "tool": tool_name,
                        "args": args,
                        "result_preview": tool_content[:LOG_PREVIEW_LENGTH],
                    }
                )
                messages.append({"role": "tool", "content": tool_content})

        # Max iterations reached without final answer
        final_usage = self.token_manager.get_usage_report(messages)
        core_logger.warning("ReAct max iterations reached", max_iterations=self.max_iterations)
        return {
            "status": "max_iterations",
            "answer": self._last_assistant_content(messages),
            "iterations": self.max_iterations,
            "steps": steps,
            "token_usage": final_usage,
        }

    @staticmethod
    def _last_assistant_content(messages: list[dict[str, Any]]) -> str:
        """Return last assistant content for partial progress."""
        for message in reversed(messages):
            if message.get("role") == "assistant" and message.get("content"):
                return message["content"]
        return "No partial result available."
