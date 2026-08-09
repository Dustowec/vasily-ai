"""Typed structures for plugin results and errors (T3-017.5).

Plugins must return PluginErrorResult (via make_error) whenever an external
backend is unavailable or a call fails. ReActLoop uses is_plugin_error to
detect structured errors and feed retry_advice back to the LLM.
"""

from typing import Any, NotRequired, TypedDict

ERROR_TYPES = (
    "backend_unavailable",
    "http_error",
    "connection_failed",
    "timeout",
    "rate_limit",
    "invalid_url",
)


class PluginErrorResult(TypedDict):
    """Structured error returned by plugins on failure."""

    status: str
    error_type: str
    message: str
    retry_advice: str
    http_status: NotRequired[int]


def make_error(
    error_type: str,
    message: str,
    retry_advice: str,
    http_status: int | None = None,
) -> PluginErrorResult:
    """Build a validated PluginErrorResult."""
    if error_type not in ERROR_TYPES:
        raise ValueError(f"Unknown error_type: {error_type}")

    result: PluginErrorResult = {
        "status": "error",
        "error_type": error_type,
        "message": message,
        "retry_advice": retry_advice,
    }
    if http_status is not None:
        result["http_status"] = http_status
    return result


def is_plugin_error(result: Any) -> bool:
    """True if result is a structured plugin error."""
    return (
        isinstance(result, dict)
        and result.get("status") == "error"
        and result.get("error_type") in ERROR_TYPES
    )
