"""
Crash Reporter - generates crash reports on fatal errors.

Features:
- Reads last N lines from log files
- Filters logs by request_id (tracks one request across all 4 sources)
- Filters logs by alert_level (shows CRITICAL_WARNING and CRASH first)
- Generates JSON + Markdown reports
- Saves to logs/crash_reports/
"""

import json
import sys
import traceback as tb
from datetime import datetime
from pathlib import Path

# Log file names (must match logging_config.py)
LOG_FILES = {
    "core": "core.log",
    "interaction": "interaction.log",
    "plugins": "plugins.log",
    "llm": "llm.log",
}

CRASH_REPORT_DIR = "crash_reports"
MAX_LOG_LINES = 50  # Last N lines to scan for filtering

# Alert level priorities (higher = more critical)
ALERT_PRIORITIES = {
    "CRASH": 5,
    "CRITICAL_WARNING": 4,
    "WARNING": 3,
    "REQUEST": 2,
    "STATE": 1,
    "DEBUG": 0,
}


class CrashReporter:
    """Generates crash reports on fatal errors."""

    def __init__(self, log_dir: Path, max_log_lines: int = MAX_LOG_LINES):
        self.log_dir = log_dir
        self.max_log_lines = max_log_lines
        self.crash_report_dir = log_dir / CRASH_REPORT_DIR
        self.crash_report_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_recent_logs(self, recent_logs: dict) -> dict:
        """Apply sanitization to recent log lines before saving."""
        from core.config import Config

        config = Config.load()
        if not config.sanitize_logs:
            return recent_logs

        redact_keys = set(config.log_redact_keys)
        sensitive_keys = set(config.log_sensitive_keys)
        max_len = config.max_log_field_length

        sanitized = {}
        for category, logs in recent_logs.items():
            if not isinstance(logs, list):
                sanitized[category] = logs
                continue

            new_logs = []
            for line in logs:
                # Parse JSON if possible
                if isinstance(line, str):
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        new_logs.append(line)
                        continue
                elif isinstance(line, dict):
                    data = line
                else:
                    new_logs.append(line)
                    continue

                # Sanitize data
                for key in list(data.keys()):
                    if key in redact_keys:
                        data[key] = "[REDACTED]"
                    elif key in sensitive_keys:
                        value = data.get(key)
                        if isinstance(value, str) and len(value) > max_len:
                            data[key] = value[:max_len] + "..."

                # Convert back to JSON string if it was originally a string
                if isinstance(line, str):
                    new_logs.append(json.dumps(data, ensure_ascii=False))
                else:
                    new_logs.append(data)

            sanitized[category] = new_logs

        return sanitized

    def generate_report(self, error: BaseException, request_id: str = None) -> tuple:
        """
        Generate crash report in JSON and Markdown formats.

        Args:
            error: The exception that caused the crash
            request_id: Optional request_id to filter logs by

        Returns:
            Tuple of (json_path, markdown_path)
        """
        timestamp = datetime.now()
        timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")

        # Try to get current request_id if not provided
        if request_id is None:
            request_id = self._get_current_request_id()

        # Collect data
        error_info = self._collect_error_info(error)
        system_info = self._collect_system_info()
        recent_logs = self._collect_recent_logs(request_id=request_id)
        critical_alerts = self._extract_critical_alerts(recent_logs)

        # Build report
        report = {
            "timestamp": timestamp.isoformat(),
            "request_id": request_id or "unknown",
            "error": error_info,
            "system": system_info,
            "critical_alerts": critical_alerts,
            "recent_logs": recent_logs,
        }

        # Sanitize recent logs before saving
        report["recent_logs"] = self._sanitize_recent_logs(report["recent_logs"])

        # Save JSON report
        json_path = self.crash_report_dir / f"crash_{timestamp_str}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)

        # Save Markdown report
        md_path = self.crash_report_dir / f"crash_{timestamp_str}.md"
        md_content = self._generate_markdown(report)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        return json_path, md_path

    def _get_current_request_id(self) -> str:
        """Try to get current request_id from structlog contextvars."""
        try:
            import structlog

            ctx = structlog.contextvars.get_contextvars()
            return ctx.get("request_id", "unknown")
        except Exception:
            return "unknown"

    def _collect_error_info(self, error: BaseException) -> dict:
        """Collect information about the error with full traceback."""
        # Используем format_exception с переданным исключением
        tb_lines = tb.format_exception(type(error), error, error.__traceback__)
        tb_text = "".join(tb_lines)

        return {
            "type": type(error).__name__,
            "message": str(error),
            "traceback": tb_text,
        }

    def _collect_system_info(self) -> dict:
        """Collect system information."""
        import platform

        return {
            "python_version": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        }

    def _collect_recent_logs(self, request_id: str = None) -> dict:
        """
        Collect recent logs from each log file.
        If request_id is provided, filter by it.
        """
        recent_logs = {}

        for category, filename in LOG_FILES.items():
            log_path = self.log_dir / filename
            if log_path.exists():
                try:
                    with open(log_path, encoding="utf-8") as f:
                        lines = f.readlines()

                    # Take last N lines to scan
                    scan_lines = lines[-self.max_log_lines :]

                    if request_id and request_id != "unknown":
                        # Filter by request_id
                        filtered = [line.strip() for line in scan_lines if request_id in line]
                        recent_logs[category] = (
                            filtered if filtered else ["No logs found for this request_id"]
                        )
                    else:
                        # No filter, take last 20 lines
                        recent_logs[category] = [line.strip() for line in scan_lines[-20:]]
                except Exception as e:
                    recent_logs[category] = [f"Error reading log: {e}"]
            else:
                recent_logs[category] = ["Log file not found"]

        return recent_logs

    def _extract_critical_alerts(self, recent_logs: dict) -> list:
        """
        Extract CRITICAL_WARNING and CRASH alerts from all logs.
        Sort by priority (CRASH first, then CRITICAL_WARNING).
        """
        critical = []

        for category, logs in recent_logs.items():
            for log_line in logs:
                try:
                    log_entry = json.loads(log_line)
                    alert = log_entry.get("alert_level", "")
                    if alert in ("CRASH", "CRITICAL_WARNING"):
                        critical.append(
                            {
                                "alert_level": alert,
                                "category": category,
                                "timestamp": log_entry.get("timestamp", ""),
                                "module": log_entry.get("module", ""),
                                "event": log_entry.get("event", ""),
                                "priority": ALERT_PRIORITIES.get(alert, 0),
                            }
                        )
                except (json.JSONDecodeError, TypeError):
                    continue  # Skip non-JSON lines

        # Sort by priority (highest first)
        critical.sort(key=lambda x: x["priority"], reverse=True)
        return critical[:10]  # Max 10 critical alerts

    def _generate_markdown(self, report: dict) -> str:
        """Generate Markdown report."""
        lines = []
        lines.append(f"# Crash Report: {report['timestamp']}")
        lines.append("")
        lines.append(f"**Request ID:** `{report['request_id']}`")
        lines.append("")

        # Critical alerts section (most important!)
        lines.append("## 🚨 Critical Alerts")
        if report["critical_alerts"]:
            lines.append("| Time | Level | Category | Module | Event |")
            lines.append("|------|-------|----------|--------|-------|")
            for alert in report["critical_alerts"]:
                icon = "🔴" if alert["alert_level"] == "CRASH" else "🟠"
                lines.append(
                    f"| {alert['timestamp'][:19]} | {icon} {alert['alert_level']} "
                    f"| {alert['category']} | {alert['module']} | {alert['event']} |"
                )
        else:
            lines.append("No critical alerts found.")
        lines.append("")

        # Error summary
        lines.append("## Error Summary")
        lines.append(f"- **Type:** `{report['error']['type']}`")
        lines.append(f"- **Message:** {report['error']['message']}")
        lines.append("")

        # System info
        lines.append("## System Info")
        lines.append(f"- **Python:** {report['system']['python_version']}")
        lines.append(f"- **Platform:** {report['system']['platform']}")
        lines.append("")

        # Traceback
        lines.append("## Traceback")
        lines.append("```")
        lines.append(report["error"]["traceback"])
        lines.append("```")
        lines.append("")

        # Recent logs by category
        lines.append("## Recent Logs (filtered by request_id)")
        for category, logs in report["recent_logs"].items():
            lines.append(f"### {category.upper()}")
            lines.append("```")
            for log in logs:
                lines.append(log)
            lines.append("```")
            lines.append("")

        return "\n".join(lines)


def install_crash_handler(log_dir: Path, max_log_lines: int = MAX_LOG_LINES) -> None:
    """
    Install global exception handler that generates crash reports.

    Args:
        log_dir: Directory for log files
    """
    reporter = CrashReporter(log_dir, max_log_lines=max_log_lines)

    def exception_handler(exc_type, exc_value, exc_traceback):
        try:
            json_path, md_path = reporter.generate_report(exc_value)
            print(f"\n{'='*60}", file=sys.stderr)
            print("FATAL ERROR - Crash report generated:", file=sys.stderr)
            print(f"  JSON: {json_path}", file=sys.stderr)
            print(f"  Markdown: {md_path}", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
        except Exception as e:
            print(f"Failed to generate crash report: {e}", file=sys.stderr)

        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = exception_handler


def install_async_exception_handler(loop, log_dir: Path) -> None:
    """
    Install loop-level handler that generates crash reports
    for unhandled asyncio task exceptions.

    Args:
        loop: Running event loop
        log_dir: Directory for log files
    """
    reporter = CrashReporter(log_dir)

    def handler(loop, context):
        exception = context.get("exception")
        message = context.get("message", "Unhandled async exception")
        error = exception if isinstance(exception, Exception) else RuntimeError(message)
        try:
            json_path, md_path = reporter.generate_report(error)
            print(f"\n[ASYNC CRASH] Report generated: {md_path}")
        except Exception as e:
            print(f"[ASYNC CRASH] Failed to generate report: {e}")

    loop.set_exception_handler(handler)
