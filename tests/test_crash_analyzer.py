"""Tests for TZ-026: local crash report analysis via LLM."""

import json
from pathlib import Path

from core.crash_analyzer import CrashAnalyzer


class FakeLLM:
    def __init__(self, response="LLM ANALYSIS"):
        self.calls = []
        self.response = response

    async def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        return {"response": self.response}


def write_report(
    crash_dir: Path,
    filename: str,
    error_type: str = "ZeroDivisionError",
    message: str = "division by zero",
) -> Path:
    report = {
        "timestamp": "2026-08-11T00:00:00",
        "request_id": "req-test",
        "error": {
            "type": error_type,
            "message": message,
            "traceback": "Traceback...\nZeroDivisionError: division by zero",
        },
        "system": {},
        "critical_alerts": [],
        "recent_logs": {},
    }

    path = crash_dir / filename
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_find_latest_report_returns_none_when_empty(tmp_path):
    analyzer = CrashAnalyzer(FakeLLM())

    assert analyzer.find_latest_report(tmp_path) is None


def test_find_latest_report_selects_newest_file(tmp_path):
    crash_dir = tmp_path / "crash_reports"
    crash_dir.mkdir()

    write_report(crash_dir, "crash_20260101_000000.json")
    write_report(crash_dir, "crash_20260102_000000.json")

    analyzer = CrashAnalyzer(FakeLLM())
    latest = analyzer.find_latest_report(tmp_path)

    assert latest is not None
    assert latest.name == "crash_20260102_000000.json"


async def test_analyze_latest_returns_llm_analysis(tmp_path):
    crash_dir = tmp_path / "crash_reports"
    crash_dir.mkdir()

    write_report(
        crash_dir,
        "crash_20260101_000000.json",
        error_type="ValueError",
        message="bad input",
    )

    llm = FakeLLM("LIKELY CAUSE: bad input")
    analyzer = CrashAnalyzer(llm)

    result = await analyzer.analyze_latest(tmp_path)

    assert result is not None
    assert result["report"] == "crash_20260101_000000.json"
    assert result["analysis"] == "LIKELY CAUSE: bad input"
    assert len(llm.calls) == 1

    prompt = llm.calls[0]
    assert "ValueError" in prompt
    assert "bad input" in prompt


async def test_analyze_latest_no_reports_returns_none(tmp_path):
    analyzer = CrashAnalyzer(FakeLLM())

    result = await analyzer.analyze_latest(tmp_path)

    assert result is None
