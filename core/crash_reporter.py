"""
Crash Reporter - generates crash reports on fatal errors.

Features:
- Reads last N lines from log files
- Filters logs by request_id (tracks one request across all 4 sources)
- Filters logs by alert_level (shows CRITICAL_WARNING and CRASH first)
- Generates JSON + Markdown reports
- Saves to logs/crash_reports/YYYY-MM-DD/ with sequential numbering
- QA mode: prevents deletion during QA phase
- TTL deletion: removes folders older than 48 hours (after QA)
"""

import json
import sys
import traceback as tb
from datetime import datetime, timedelta
from pathlib import Path

# Log file names (must match logging_config.py)
LOG_FILES = {
    "core": "core.log",
    "interaction": "interaction.log",
    "plugins": "plugins.log",
    "llm": "llm.log",
}

CRASH_REPORT_DIR = "crash_reports"
MAX_LOG_LINES = 50
MAX_REPORTS_PER_FOLDER = 100
TTL_HOURS = 48
QA_MODE = True  # Во время фазы QA: не удалять

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

    def _get_report_folder(self) -> Path:
        """Get folder for today's reports: logs/crash_reports/YYYY-MM-DD/"""
        today = datetime.now().strftime("%Y-%m-%d")
        folder = self.crash_report_dir / today
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _get_next_number(self, folder: Path) -> int:
        """Find the next available number for crash_XXX files."""
        if not folder.exists():
            return 1

        existing = list(folder.glob("crash_*.json"))
        if not existing:
            return 1

        numbers = []
        for p in existing:
            try:
                # extract number from crash_123.json
                name = p.stem  # crash_123
                num = int(name.split("_")[1])
                numbers.append(num)
            except (IndexError, ValueError):
                continue

        if not numbers:
            return 1

        return max(numbers) + 1

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

                for key in list(data.keys()):
                    if key in redact_keys:
                        data[key] = "[REDACTED]"
                    elif key in sensitive_keys:
                        value = data.get(key)
                        if isinstance(value, str) and len(value) > max_len:
                            data[key] = value[:max_len] + "..."

                if isinstance(line, str):
                    new_logs.append(json.dumps(data, ensure_ascii=False))
                else:
                    new_logs.append(data)

            sanitized[category] = new_logs

        return sanitized

    def generate_report(self, error: BaseException, request_id: str = None) -> tuple:
        """
        Generate crash report in JSON and Markdown formats.
        Saves to logs/crash_reports/YYYY-MM-DD/crash_XXX.json|md
        """
        timestamp = datetime.now()

        if request_id is None:
            request_id = self._get_current_request_id()

        # Get today's folder and next number
        folder = self._get_report_folder()
        num = self._get_next_number(folder)

        # Enforce limit
        if num > MAX_REPORTS_PER_FOLDER:
            # Start a new folder with suffix
            folder = self.crash_report_dir / f"{datetime.now().strftime('%Y-%m-%d')}_2"
            folder.mkdir(parents=True, exist_ok=True)
            num = 1

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
        json_path = folder / f"crash_{num:03d}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)

        # Save Markdown report
        md_path = folder / f"crash_{num:03d}.md"
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
        """Collect recent logs from each log file."""
        recent_logs = {}

        for category, filename in LOG_FILES.items():
            log_path = self.log_dir / filename
            if log_path.exists():
                try:
                    with open(log_path, encoding="utf-8") as f:
                        lines = f.readlines()

                    scan_lines = lines[-self.max_log_lines :]

                    if request_id and request_id != "unknown":
                        filtered = [line.strip() for line in scan_lines if request_id in line]
                        recent_logs[category] = (
                            filtered if filtered else ["No logs found for this request_id"]
                        )
                    else:
                        recent_logs[category] = [line.strip() for line in scan_lines[-20:]]
                except Exception as e:
                    recent_logs[category] = [f"Error reading log: {e}"]
            else:
                recent_logs[category] = ["Log file not found"]

        return recent_logs

    def _extract_critical_alerts(self, recent_logs: dict) -> list:
        """Extract CRITICAL_WARNING and CRASH alerts from all logs."""
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
                    continue

        critical.sort(key=lambda x: x["priority"], reverse=True)
        return critical[:10]

    def _generate_markdown(self, report: dict) -> str:
        """Generate Markdown report."""
        lines = []
        lines.append(f"# Crash Report: {report['timestamp']}")
        lines.append("")
        lines.append(f"**Request ID:** `{report['request_id']}`")
        lines.append("")

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

        lines.append("## Error Summary")
        lines.append(f"- **Type:** `{report['error']['type']}`")
        lines.append(f"- **Message:** {report['error']['message']}")
        lines.append("")

        lines.append("## System Info")
        lines.append(f"- **Python:** {report['system']['python_version']}")
        lines.append(f"- **Platform:** {report['system']['platform']}")
        lines.append("")

        lines.append("## Traceback")
        lines.append("```")
        lines.append(report["error"]["traceback"])
        lines.append("```")
        lines.append("")

        lines.append("## Recent Logs (filtered by request_id)")
        for category, logs in report["recent_logs"].items():
            lines.append(f"### {category.upper()}")
            lines.append("```")
            for log in logs:
                lines.append(log)
            lines.append("```")
            lines.append("")

        return "\n".join(lines)

    def clean_old_reports(self) -> int:
        """Delete crash report folders older than TTL_HOURS.
        Only runs when QA_MODE is False.
        """
        if QA_MODE:
            return 0

        cutoff = datetime.now() - timedelta(hours=TTL_HOURS)
        deleted = 0

        for folder in self.crash_report_dir.iterdir():
            if not folder.is_dir():
                continue
            try:
                # Parse date from folder name (YYYY-MM-DD or YYYY-MM-DD_N)
                folder_date_str = folder.name.split("_")[0]
                folder_date = datetime.strptime(folder_date_str, "%Y-%m-%d")
                if folder_date < cutoff:
                    import shutil

                    shutil.rmtree(folder)
                    deleted += 1
            except (ValueError, OSError):
                continue

        return deleted


def install_crash_handler(log_dir: Path, max_log_lines: int = MAX_LOG_LINES) -> None:
    """Install global exception handler that generates crash reports."""
    reporter = CrashReporter(log_dir, max_log_lines=max_log_lines)

    def exception_handler(exc_type, exc_value, exc_traceback):
        try:
            json_path, md_path = reporter.generate_report(exc_value)
            print(f"\n{'=' * 60}", file=sys.stderr)
            print("FATAL ERROR - Crash report generated:", file=sys.stderr)
            print(f"  JSON: {json_path}", file=sys.stderr)
            print(f"  Markdown: {md_path}", file=sys.stderr)
            print(f"{'=' * 60}\n", file=sys.stderr)
        except Exception as e:
            print(f"Failed to generate crash report: {e}", file=sys.stderr)

        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = exception_handler


def install_async_exception_handler(loop, log_dir: Path) -> None:
    """Install loop-level handler for unhandled asyncio task exceptions."""
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
