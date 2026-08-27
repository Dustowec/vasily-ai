"""ReAct loop - Reasoning + Acting pattern (T3-016).
Mandatory requirements implemented:
- R1: Plugin errors caught and returned to LLM history, cycle continues.
- R2: Tool call limit (config.max_tool_calls_per_tool), force stop on exceed.
- R3: KeyboardInterrupt/CancelledError handled, returns partial progress.
- R4: Step logging across core/llm/interaction journals with request_id.
- Token management: pair-safe trimming, script-aware tokens (T3-018).
- Golden Prompts: curated system prompts per task type (T3-020).
- P1-3: mock data blocked outside dev_mode.
- P1-4: tool_calls kept in history, defensive arguments parsing,
session timeout, graceful LLM-unavailable result.
- T3-017.5: deduplication of identical calls with hard limit.
- P2-1: limits and preview length taken from Config.
- P3-3: strict TypedDict for ReActResult, ReActStep, TokenUsage.
- ADR-011: Parsing  tags, Sliding Window support.
"""

import asyncio
import json
import time
from typing import Any

from core.golden_prompts import GoldenPromptsLibrary
from core.logging_config import get_logger
from core.react_types import ReActResult, ReActStep
from core.token_manager import TokenManager
from integrations.ollama_client import LLMUnavailableError, OllamaClient

core_logger = get_logger("core", "ReActLoop")
llm_logger = get_logger("llm", "ReActLoop")
interaction_logger = get_logger("interaction", "ReActLoop")

DEFAULT_SYSTEM_PROMPT = (
    "You are Vasily, a helpful AI agent. You can use tools to accomplish tasks. "
    "Think step by step. If a tool is needed, call it. "
    "When you have the final answer, respond directly without tool calls. "
    "If a tool returns an error, consider another approach or explain the failure."
)


class ReActLoop:
    """Executes Reasoning + Acting cycle with the LLM and plugins."""

    def __init__(self, config, llm_client, plugin_registry):
        self.config = config
        self.llm = llm_client
        self.plugin_registry = plugin_registry
        self.max_iterations = config.max_react_iterations
        self.max_tool_calls = config.max_tool_calls_per_tool
        self.preview_length = config.log_preview_length
        self.tools = self._build_tools()
        self.token_manager = TokenManager(config.llm_num_ctx, config.llm_safety_margin)
        self.prompts_library = GoldenPromptsLibrary()
        self._history = []

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

    async def run(
        self,
        user_request: str,
        prompt_type: str = "default",
        dialogue_history: list[dict] | None = None,
    ) -> ReActResult:
        """Run the ReAct cycle for a user request.
        ADR-011: dialogue_history is a sliding window of last 5 pairs (user/assistant).
        """
        system_prompt = self.prompts_library.get_prompt(prompt_type) or DEFAULT_SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # ADR-011: Add sliding window history before current request
        if dialogue_history:
            messages.extend(dialogue_history)

        messages.append({"role": "user", "content": user_request})

        tool_call_counts: dict[str, int] = {}
        call_signatures: dict[str, int] = {}
        previous_results: dict[str, str] = {}
        steps: list[ReActStep] = []
        start_time = time.monotonic()

        core_logger.info(
            "ReAct loop started",
            max_iterations=self.max_iterations,
            prompt_type=prompt_type,
            request_preview=user_request[: self.preview_length],
        )

        for iteration in range(self.max_iterations):
            # P1-4: overall session timeout
            elapsed = time.monotonic() - start_time
            if elapsed > self.config.request_timeout:
                core_logger.warning(
                    "ReAct session timeout",
                    elapsed=round(elapsed, 1),
                    timeout=self.config.request_timeout,
                )
                return ReActResult(
                    status="timeout",
                    answer=self._last_assistant_content(messages),
                    iterations=iteration + 1,
                    steps=steps,
                    token_usage=self.token_manager.get_usage_report(messages),
                )

            core_logger.info("ReAct iteration started", iteration=iteration + 1)
            messages = self.token_manager.trim_messages(messages)
            usage = self.token_manager.get_usage_report(messages)
            core_logger.info(
                "Token usage",
                iteration=iteration + 1,
                used_tokens=usage["used_tokens"],
                usage_percent=usage["usage_percent"],
            )

            # R3: handle user interruption; P1-4: handle LLM unavailability
            try:
                response = await self.llm.chat(messages=messages, tools=self.tools)
            except (KeyboardInterrupt, asyncio.CancelledError):
                core_logger.warning("ReAct interrupted by user", iteration=iteration + 1)
                return ReActResult(
                    status="interrupted",
                    answer=self._last_assistant_content(messages),
                    iterations=iteration + 1,
                    steps=steps,
                    token_usage=usage,
                )
            except LLMUnavailableError as e:
                core_logger.error("LLM unavailable during ReAct", error=str(e))
                return ReActResult(
                    status="llm_unavailable",
                    answer="AI is temporarily unavailable. Try again later.",
                    iterations=iteration + 1,
                    steps=steps,
                    token_usage=usage,
                )

            llm_logger.info(
                "LLM response received",
                iteration=iteration + 1,
                has_tool_calls=bool(response.get("message", {}).get("tool_calls")),
            )

            message = response.get("message", {})
            tool_calls = message.get("tool_calls") or []
            content = message.get("content", "")

            # ADR-011: Extract thinking block.
            # We keep full content (with ) in history for LLM context,
            # but use clean_answer for the final result returned to user/memory.
            _, clean_answer = OllamaClient.extract_thinking_and_answer(content)

            # P1-4: keep tool_calls in history so the model tracks its actions
            # ADR-011: Keep full content (with ) in history
            assistant_message = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            messages.append(assistant_message)

            if not tool_calls:
                core_logger.info("ReAct loop finished", iterations=iteration + 1)
                return ReActResult(
                    status="success",
                    answer=clean_answer,  # ADR-011: Return clean answer
                    iterations=iteration + 1,
                    steps=steps,
                    token_usage=usage,
                )

            for tool_call in tool_calls:
                function = tool_call.get("function", {})
                tool_name = function.get("name", "")
                args = function.get("arguments", {}) or {}

                # P1-4: defensive parsing when arguments arrive as a string
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                if not isinstance(args, dict):
                    args = {}

                # T3-017.5: deduplication of identical calls
                signature = (
                    f"{tool_name}:" f"{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
                )
                identical_count = call_signatures.get(signature, 0)
                call_signatures[signature] = identical_count + 1

                if identical_count == 1:
                    tool_content = (
                        "[DUPLICATE CALL] This exact call was already made; the "
                        "result has not changed: " + previous_results.get(signature, "")
                    )
                    interaction_logger.warning("Duplicate call blocked", tool=tool_name)
                elif identical_count >= 2:
                    tool_content = (
                        "[LIMIT REACHED] This call has been attempted multiple "
                        "times with identical arguments. You must now provide a "
                        "final answer based on the information already gathered, "
                        "without retrying this tool."
                    )
                    interaction_logger.warning("Identical call limit reached", tool=tool_name)
                else:
                    # R2: per-tool limit counts only real executions
                    if tool_call_counts.get(tool_name, 0) >= self.max_tool_calls:
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
                        args_preview=str(args)[: self.preview_length],
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
                                        f"Tool '{tool_name}' backend is "
                                        "unavailable. Mock data is disabled "
                                        "outside dev_mode."
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
                                result_preview=tool_content[: self.preview_length],
                            )
                    except Exception as e:
                        interaction_logger.error("Plugin failed", tool=tool_name, error=str(e))
                        tool_content = json.dumps({"error": str(e)}, ensure_ascii=False)

                previous_results[signature] = tool_content
                steps.append(
                    ReActStep(
                        iteration=iteration + 1,
                        tool=tool_name,
                        args=args,
                        result_preview=tool_content[: self.preview_length],
                    )
                )
                messages.append({"role": "tool", "content": tool_content})

        final_usage = self.token_manager.get_usage_report(messages)
        core_logger.warning("ReAct max iterations reached", max_iterations=self.max_iterations)
        return ReActResult(
            status="max_iterations",
            answer=self._last_assistant_content(messages),
            iterations=self.max_iterations,
            steps=steps,
            token_usage=final_usage,
        )

    @staticmethod
    def _last_assistant_content(messages: list[dict[str, Any]]) -> str:
        """Return last assistant content for partial progress."""
        for message in reversed(messages):
            if message.get("role") == "assistant" and message.get("content"):
                # ADR-011: Clean thinking tags even in partial results
                _, clean = OllamaClient.extract_thinking_and_answer(message["content"])
                return clean
        return "No partial result available."
