"""
Test script for Crash Reporter.
Run: python test_crash_reporter.py
This will intentionally crash to test crash reporting.
"""

from pathlib import Path

import structlog

from core.crash_reporter import install_crash_handler
from core.logging_config import get_logger, setup_logging


def main():
    # Setup logging
    log_dir = Path("logs")
    setup_logging(log_dir=log_dir, level="DEBUG", json_logs=True)

    # Install crash handler
    install_crash_handler(log_dir)

    # Simulate some work
    logger = get_logger("core", "TestModule")
    logger.info("Starting test", test_id="crash-test-001")

    # Bind request_id
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="crash-test-001")

    # Do some logging
    logger.info("Processing data", items_count=100)
    logger.warning("Memory usage high", memory_mb=7500, threshold_mb=8000)

    # Intentionally crash!
    logger.error("About to crash", reason="division by zero")
    raise ZeroDivisionError("Test crash for crash reporter")


if __name__ == "__main__":
    main()
