"""Extended tests for CrashReporter.

Covers: report generation, folder structure, numbering,
        sanitization, async handler, cleanup (QA mode).
"""

import json
import sys
from unittest.mock import MagicMock

import pytest

from core.crash_reporter import (
    CrashReporter,
    install_async_exception_handler,
    install_crash_handler,
)


@pytest.fixture
def log_dir(tmp_path):
    """Create a temporary log directory."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    return log_dir


@pytest.fixture
def reporter(log_dir):
    """Create a CrashReporter instance."""
    return CrashReporter(log_dir)


def test_init_creates_crash_report_dir(log_dir):
    """CrashReporter should create crash_reports directory."""
    CrashReporter(log_dir)
    assert (log_dir / "crash_reports").exists()


def test_get_report_folder_creates_dated_folder(reporter, log_dir):
    """_get_report_folder should create folder with today's date."""
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    folder = reporter._get_report_folder()
    assert folder.name == today
    assert folder.parent == log_dir / "crash_reports"


def test_get_next_number_returns_1_for_empty_folder(reporter, log_dir):
    """_get_next_number should return 1 for empty folder."""
    folder = reporter._get_report_folder()
    assert reporter._get_next_number(folder) == 1


def test_get_next_number_increments_on_existing_files(reporter, log_dir):
    """_get_next_number should return max number + 1."""
    folder = reporter._get_report_folder()
    (folder / "crash_001.json").touch()
    (folder / "crash_002.json").touch()
    assert reporter._get_next_number(folder) == 3


def test_get_next_number_skips_invalid_names(reporter, log_dir):
    """_get_next_number should ignore files with invalid names."""
    folder = reporter._get_report_folder()
    (folder / "crash_001.json").touch()
    (folder / "crash_abc.json").touch()
    (folder / "other.txt").touch()
    assert reporter._get_next_number(folder) == 2


def test_generate_report_creates_json_and_md(reporter, log_dir):
    """generate_report should create both JSON and MD files."""
    error = RuntimeError("test error")
    json_path, md_path = reporter.generate_report(error)

    assert json_path.exists()
    assert md_path.exists()
    assert json_path.suffix == ".json"
    assert md_path.suffix == ".md"


def test_generate_report_creates_in_dated_folder(reporter, log_dir):
    """generate_report should save files in dated folder."""
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    error = RuntimeError("test error")
    json_path, md_path = reporter.generate_report(error)

    assert str(json_path.parent).endswith(today)
    assert json_path.name.startswith("crash_")


def test_generate_report_uses_sequential_numbers(reporter, log_dir):
    """generate_report should use sequential numbers."""
    error = RuntimeError("test error")

    json1, _ = reporter.generate_report(error)
    json2, _ = reporter.generate_report(error)

    assert json1.name == "crash_001.json"
    assert json2.name == "crash_002.json"


def test_generate_report_includes_request_id(reporter, log_dir):
    """generate_report should include request_id in report."""
    error = RuntimeError("test error")
    json_path, _ = reporter.generate_report(error, request_id="req-test-001")

    with open(json_path, encoding="utf-8") as f:
        report = json.load(f)
    assert report["request_id"] == "req-test-001"


def test_generate_report_contains_error_info(reporter, log_dir):
    """generate_report should include error type, message, traceback."""
    try:
        raise ValueError("something went wrong")
    except ValueError as e:
        error = e

    json_path, _ = reporter.generate_report(error)
    with open(json_path, encoding="utf-8") as f:
        report = json.load(f)

    assert report["error"]["type"] == "ValueError"
    assert report["error"]["message"] == "something went wrong"
    assert "traceback" in report["error"]
    assert "ValueError" in report["error"]["traceback"]


def test_generate_report_includes_system_info(reporter, log_dir):
    """generate_report should include system info."""
    error = RuntimeError("test")
    json_path, _ = reporter.generate_report(error)
    with open(json_path, encoding="utf-8") as f:
        report = json.load(f)

    assert "system" in report
    assert "python_version" in report["system"]
    assert "platform" in report["system"]


def test_generate_report_includes_critical_alerts(reporter, log_dir):
    """generate_report should extract critical alerts from logs."""
    # Создаём лог-файл с критическим алертом
    log_file = log_dir / "core.log"
    log_file.write_text(
        '{"level": "error", "event": "crash", "alert_level": "CRASH", "module": "Test", "timestamp": "2026-01-01T00:00:00"}',
        encoding="utf-8",
    )

    error = RuntimeError("test")
    json_path, _ = reporter.generate_report(error)

    with open(json_path, encoding="utf-8") as f:
        report = json.load(f)

    critical_alerts = report.get("critical_alerts", [])
    assert len(critical_alerts) >= 1
    assert critical_alerts[0]["alert_level"] == "CRASH"


def test_collect_error_info_returns_traceback(reporter):
    """_collect_error_info should return full traceback."""
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError as e:
        error = e

    info = reporter._collect_error_info(error)
    assert info["type"] == "ZeroDivisionError"
    assert info["message"] == "division by zero"
    assert "ZeroDivisionError" in info["traceback"]


def test_collect_recent_logs_returns_logs(reporter, log_dir):
    """_collect_recent_logs should return logs from files."""
    log_file = log_dir / "core.log"
    log_file.write_text("log line 1\nlog line 2\n", encoding="utf-8")

    logs = reporter._collect_recent_logs()
    assert "core" in logs
    assert len(logs["core"]) > 0


def test_collect_recent_logs_filters_by_request_id(reporter, log_dir):
    """_collect_recent_logs should filter by request_id."""
    log_file = log_dir / "core.log"
    log_file.write_text(
        '{"request_id": "req-001", "event": "test1"}\n'
        '{"request_id": "req-002", "event": "test2"}\n',
        encoding="utf-8",
    )

    logs = reporter._collect_recent_logs(request_id="req-001")
    assert "core" in logs
    for line in logs["core"]:
        assert "req-001" in line


def test_sanitize_recent_logs_redacts_sensitive_keys(reporter, log_dir):
    """_sanitize_recent_logs should redact sensitive keys."""
    logs = {"core": ['{"password": "secret", "user": "alex", "event": "test"}']}

    sanitized = reporter._sanitize_recent_logs(logs)
    data = json.loads(sanitized["core"][0])
    assert data["password"] == "[REDACTED]"
    assert data["user"] == "alex"


def test_install_crash_handler_sets_excepthook(log_dir):
    """install_crash_handler should set sys.excepthook."""
    original_hook = sys.excepthook
    try:
        install_crash_handler(log_dir)
        assert sys.excepthook is not original_hook
    finally:
        sys.excepthook = original_hook


def test_install_async_exception_handler_sets_loop_handler(log_dir):
    """install_async_exception_handler should set loop exception handler."""
    loop = MagicMock()
    install_async_exception_handler(loop, log_dir)
    loop.set_exception_handler.assert_called_once()


def test_clean_old_reports_skips_in_qa_mode(reporter, log_dir):
    """clean_old_reports should do nothing in QA mode."""
    # Создаём папку старше TTL
    from datetime import datetime, timedelta

    old_date = datetime.now() - timedelta(days=10)
    old_folder = log_dir / "crash_reports" / old_date.strftime("%Y-%m-%d")
    old_folder.mkdir(parents=True)

    # QA_MODE = True, удаление отключено
    deleted = reporter.clean_old_reports()
    assert deleted == 0
    assert old_folder.exists()


def test_markdown_report_generation(reporter, log_dir):
    """generate_report should create readable Markdown."""
    error = RuntimeError("test error")
    _, md_path = reporter.generate_report(error, request_id="req-test")

    content = md_path.read_text(encoding="utf-8")
    assert "# Crash Report" in content
    assert "req-test" in content
    assert "RuntimeError" in content
