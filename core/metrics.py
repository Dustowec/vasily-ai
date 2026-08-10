"""Metrics collector for TZ-023."""

from typing import Any

BLOCKED_MARKERS = ("[DUPLICATE CALL]", "[LIMIT REACHED]")


class MetricsCollector:
    """Collects extended agent metrics."""

    def __init__(self) -> None:
        self._requests_total = 0
        self._requests_success = 0
        self._requests_error = 0
        self._requests_interrupted = 0
        self._total_duration_ms = 0.0
        self._iterations_total = 0
        self._tool_calls_total = 0
        self._tool_calls_blocked = 0
        self._tokens_used_total = 0

    def record_request(
        self,
        duration_ms: float,
        status: str,
        iterations: int = 0,
    ) -> None:
        """Record one finished request."""
        self._requests_total += 1
        self._total_duration_ms += float(duration_ms)

        if status == "success":
            self._requests_success += 1
        elif status == "error":
            self._requests_error += 1
        elif status == "interrupted":
            self._requests_interrupted += 1

        self._iterations_total += int(iterations)

    def record_react_result(self, result: dict[str, Any]) -> None:
        """Record ReAct loop result details."""
        steps = result.get("steps", []) or []
        self._tool_calls_total += len(steps)

        for step in steps:
            preview = str(step.get("result_preview", ""))
            if preview.startswith(BLOCKED_MARKERS):
                self._tool_calls_blocked += 1

        token_usage = result.get("token_usage", {}) or {}
        self._tokens_used_total += int(token_usage.get("used_tokens", 0))

        self._iterations_total += int(result.get("iterations", 0))

    def snapshot(self) -> dict[str, Any]:
        """Return current metrics as a plain dictionary."""
        if self._requests_total > 0:
            avg_duration_ms = round(self._total_duration_ms / self._requests_total, 2)
        else:
            avg_duration_ms = 0.0

        return {
            "requests_total": self._requests_total,
            "requests_success": self._requests_success,
            "requests_error": self._requests_error,
            "requests_interrupted": self._requests_interrupted,
            "avg_duration_ms": avg_duration_ms,
            "iterations_total": self._iterations_total,
            "tool_calls_total": self._tool_calls_total,
            "tool_calls_blocked": self._tool_calls_blocked,
            "tokens_used_total": self._tokens_used_total,
        }
