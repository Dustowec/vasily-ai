"""Crash Analyzer - local LLM analysis of crash reports (TZ-026).

Reads the latest crash report JSON and asks the local LLM
for a short diagnostic summary. No external services used.
"""

import json
from pathlib import Path

from core.logging_config import get_logger

logger = get_logger("core", "CrashAnalyzer")

MAX_TRACEBACK_CHARS = 2000


class CrashAnalyzer:
    """Analyzes crash reports using a local LLM."""

    def __init__(self, llm_client):
        self.llm = llm_client

    def find_latest_report(self, log_dir: Path) -> Path | None:
        """Find the newest crash_*.json inside log_dir/crash_reports.

        Returns None when the folder is absent or empty.
        """
        crash_dir = Path(log_dir) / "crash_reports"
        if not crash_dir.exists():
            return None

        reports = sorted(
            crash_dir.glob("crash_*.json"),
            key=lambda p: p.name,
            reverse=True,
        )
        return reports[0] if reports else None

    def _build_prompt(self, report: dict) -> str:
        """Build a short diagnostic prompt from a crash report."""
        error = report.get("error", {})
        error_type = error.get("type", "UnknownError")
        message = error.get("message", "No message")
        traceback_text = error.get("traceback", "")

        if len(traceback_text) > MAX_TRACEBACK_CHARS:
            traceback_text = traceback_text[:MAX_TRACEBACK_CHARS] + "..."

        prompt = (
            "You are a senior Python developer. "
            "Analyze the following crash report and provide:\n"
            "1. A one-line summary of what happened.\n"
            "2. The most likely root cause.\n"
            "3. One concrete recommendation to prevent this crash.\n\n"
            f"Error type: {error_type}\n"
            f"Error message: {message}\n"
            f"Traceback:\n{traceback_text}\n"
        )
        return prompt

    async def analyze_latest(self, log_dir: Path) -> dict | None:
        """Analyze the latest crash report.

        Returns None when there are no reports.
        Returns {"report": <filename>, "analysis": <llm_text>} on success.
        """
        report_path = self.find_latest_report(log_dir)
        if report_path is None:
            logger.info("No crash reports found", log_dir=str(log_dir))
            return None

        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to read crash report", error=str(e))
            return None

        prompt = self._build_prompt(report)
        response = await self.llm.generate(prompt)
        analysis = response.get("response", "").strip()

        logger.info(
            "Crash report analyzed",
            report=report_path.name,
            analysis_length=len(analysis),
        )

        return {
            "report": report_path.name,
            "analysis": analysis,
        }
