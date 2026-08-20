"""Failure tests for async exception handling (Sprint 1, Step 2)."""

import asyncio

from core.crash_reporter import install_async_exception_handler


async def test_async_exception_handler_creates_crash_report(tmp_path):
    """Unhandled asyncio task exception must produce a crash report."""
    loop = asyncio.get_running_loop()
    install_async_exception_handler(loop, tmp_path)

    handler = loop.get_exception_handler()
    assert handler is not None

    try:
        raise RuntimeError("task exploded")
    except RuntimeError as e:
        exc = e

    handler(loop, {"message": "Task exception was never retrieved", "exception": exc})

    reports = list((tmp_path / "crash_reports").glob("**/*.json"))
    assert len(reports) >= 1
