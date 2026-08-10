"""Typed structures for ReAct loop results (P3-3)."""

from typing import Any, TypedDict


class TokenUsage(TypedDict):
    """Token usage report from TokenManager."""

    used_tokens: int
    max_tokens: int
    usage_percent: float
    available_tokens: int


class ReActStep(TypedDict):
    """One step in the ReAct cycle (tool call and result)."""

    iteration: int
    tool: str
    args: dict[str, Any]
    result_preview: str


class ReActResult(TypedDict):
    """Final result from ReActLoop.run()."""

    status: str
    answer: str
    iterations: int
    steps: list[ReActStep]
    token_usage: TokenUsage
