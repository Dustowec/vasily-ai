"""
Crash Reporter - generates crash reports on fatal errors.

Variant A (Phase 1):
- Reads last N lines from log files
- Generates JSON + Markdown reports
- Saves to logs/crash_reports/
- NO cloud sending (local only)
"""

import json
import sys
import traceback
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
MAX_LOG_LINES = 20  # Last N lines to include in report


class CrashReporter:
    """Generates crash reports on fatal errors."""

    def __init__(self, log_dir: Path, max_log_lines: int = MAX_LOG_LINES):
        self.log_dir = log_dir
        self.max_log_lines = max_log_lines
        self.crash_report_dir = log_dir / CRASH_REPORT_DIR
        self.crash_report_dir.mkdir(parents=True, exist_ok=True)

    def generate_report(self, error: BaseException) -> tuple:
        """
        Generate crash report in JSON and Markdown formats.

        Args:
            error: The exception that caused the crash

        Returns:
            Tuple of (json_path, markdown_path)
        """
        timestamp = datetime.now()
        timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")

        # Collect data
        error_info = self._collect_error_info(error)
        system_info = self._collect_system_info()
        recent_logs = self._collect_recent_logs()

        # Build report
        report = {
            "timestamp": timestamp.isoformat(),
            "error": error_info,
            "system": system_info,
            "recent_logs": recent_logs,
        }

        # Save JSON report
        json_path = self.crash_report_dir / f"crash_{timestamp_str}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        # Save Markdown report
        md_path = self.crash_report_dir / f"crash_{timestamp_str}.md"
        md_content = self._generate_markdown(report)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        return json_path, md_path

    def _collect_error_info(self, error: BaseException) -> dict:
        """Collect information about the error."""
        return {
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
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

    def _collect_recent_logs(self) -> dict:
        """Collect last N lines from each log file."""
        recent_logs = {}

        for category, filename in LOG_FILES.items():
            log_path = self.log_dir / filename
            if log_path.exists():
                try:
                    with open(log_path, encoding="utf-8") as f:
                        lines = f.readlines()
                        recent_logs[category] = [
                            line.strip() for line in lines[-self.max_log_lines :]
                        ]
                except Exception as e:
                    recent_logs[category] = [f"Error reading log: {e}"]
            else:
                recent_logs[category] = ["Log file not found"]

        return recent_logs

    def _generate_markdown(self, report: dict) -> str:
        """Generate Markdown report."""
        lines = []
        lines.append(f"# Crash Report: {report['timestamp']}")
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

        # Recent logs
        lines.append("## Recent Logs")
        for category, logs in report["recent_logs"].items():
            lines.append(f"### {category.upper()}")
            lines.append("```")
            for log in logs:
                lines.append(log)
            lines.append("```")
            lines.append("")

        return "\n".join(lines)


def install_crash_handler(log_dir: Path) -> None:
    """
    Install global exception handler that generates crash reports.

    Args:
        log_dir: Directory for log files
    """
    reporter = CrashReporter(log_dir)

    def exception_handler(exc_type, exc_value, exc_traceback):
        # Generate crash report
        try:
            json_path, md_path = reporter.generate_report(exc_value)
            print(f"\n{'='*60}", file=sys.stderr)
            print("FATAL ERROR - Crash report generated:", file=sys.stderr)
            print(f"  JSON: {json_path}", file=sys.stderr)
            print(f"  Markdown: {md_path}", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
        except Exception as e:
            print(f"Failed to generate crash report: {e}", file=sys.stderr)

        # Call default handler
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = exception_handler
