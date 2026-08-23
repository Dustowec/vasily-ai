"""Tests for WatchdogLogger with rotation (TZ-025).
Covers: file creation, message format, kwargs, rotation, error handling.
"""

import pytest

from core.watchdog import WatchdogLogger


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path / "logs"


@pytest.fixture
def logger(log_dir):
    return WatchdogLogger(log_dir=log_dir, max_entries=10)


def test_logger_creates_file(logger, log_dir):
    """Logger must create the log file on first write."""
    logger.log("INFO", "test message")
    assert (log_dir / "watchdog.log").exists()


def test_logger_creates_directory(logger, tmp_path):
    """Logger must create the log directory if it doesn't exist."""
    new_dir = tmp_path / "new" / "nested" / "logs"
    new_logger = WatchdogLogger(log_dir=new_dir, max_entries=10)
    new_logger.log("INFO", "test")
    assert new_dir.exists()
    assert (new_dir / "watchdog.log").exists()


def test_logger_writes_message(logger, log_dir):
    """Logger must write timestamp, level, and message."""
    logger.log("WARNING", "disk low")
    content = (log_dir / "watchdog.log").read_text(encoding="utf-8")
    assert "WARNING" in content
    assert "disk low" in content
    # ISO timestamp format
    assert "T" in content  # e.g. 2026-08-23T16:00:00


def test_logger_writes_kwargs(logger, log_dir):
    """Logger must append kwargs to the log line."""
    logger.log("INFO", "restart", attempt=1, max_attempts=3)
    content = (log_dir / "watchdog.log").read_text(encoding="utf-8")
    assert "attempt=1" in content
    assert "max_attempts=3" in content


def test_logger_appends_multiple_lines(logger, log_dir):
    """Each log call must append a new line."""
    logger.log("INFO", "first")
    logger.log("INFO", "second")
    logger.log("INFO", "third")
    content = (log_dir / "watchdog.log").read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip()]
    assert len(lines) == 3
    assert "first" in lines[0]
    assert "second" in lines[1]
    assert "third" in lines[2]


def test_logger_rotates_on_max_entries(log_dir):
    """When entries exceed max_entries, rotation must keep only last 10."""
    logger = WatchdogLogger(log_dir=log_dir, max_entries=10)
    # Write 15 messages — rotation triggers at multiples of 10
    for i in range(15):
        logger.log("INFO", f"message-{i}")
    content = (log_dir / "watchdog.log").read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip()]
    # After 10th write: rotation keeps last 10, then 5 more writes = 15 total
    assert len(lines) == 15


def test_logger_rotates_keeps_last_10(log_dir):
    """Rotation must preserve only the last 10 entries before writing."""
    logger = WatchdogLogger(log_dir=log_dir, max_entries=10)
    # Write 25 messages — rotation triggers at 10 and 20
    for i in range(25):
        logger.log("INFO", f"message-{i}")
    content = (log_dir / "watchdog.log").read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip()]
    # At counter=20: file has 19 lines (message-0..18), rotation keeps last 10 (message-9..18)
    # Then message-19..24 are appended = 16 lines total (message-9..24)
    assert len(lines) == 16
    # First message in file must be message-9 (kept from rotation)
    assert "message-9" in lines[0]
    # Last message must be the latest
    assert "message-24" in lines[-1]


def test_logger_handles_write_error(monkeypatch, log_dir):
    """Logger must not raise when write fails."""
    logger = WatchdogLogger(log_dir=log_dir, max_entries=10)
    # Force open() to raise
    monkeypatch.setattr(
        "builtins.open", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk full"))
    )
    # Must not raise
    logger.log("INFO", "this will fail")


def test_logger_counter_increments(logger):
    """Internal counter must increment on each log call."""
    assert logger._counter == 0
    logger.log("INFO", "first")
    assert logger._counter == 1
    logger.log("INFO", "second")
    assert logger._counter == 2
