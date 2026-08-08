"""
Test script for alert levels and log rotation.
Run: python test_alert_levels.py
"""

import asyncio
from pathlib import Path

import structlog

from core.logging_config import get_logger, setup_logging


async def test_all_alert_levels():
    """Test all 5 alert levels."""
    # Bind request_id
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="test-alerts-001")

    # Get loggers
    core_logger = get_logger("core", "AgentCore")
    interaction_logger = get_logger("interaction", "AgentCore")
    plugin_logger = get_logger("plugins", "DanbooruTool")
    llm_logger = get_logger("llm", "OllamaClient")

    # 1. STATE (auto-detected from INFO)
    core_logger.info("Agent started", plugins_count=4, version="0.1.0")

    # 2. REQUEST (auto-detected from INFO + keyword "request")
    core_logger.info("User request received", request_type="art_generation")

    # 3. WARNING (auto-detected from WARNING)
    plugin_logger.warning("Low memory", memory_mb=512, threshold_mb=1024)

    # 4. CRITICAL_WARNING (auto-detected from ERROR)
    llm_logger.error("LLM not responding", retries=3, timeout=30)

    # 5. Manual override: REQUEST instead of STATE
    interaction_logger.info("Processing started", alert_level="REQUEST", task="art_generation")

    # Simulate some work
    await asyncio.sleep(0.5)

    # Final state
    core_logger.info("Request completed", status="success", duration_ms=500)

    print("\n" + "=" * 60)
    print("Alert levels test complete!")
    print("Check logs in: logs/")
    print("  - Look for 'alert_level' field in JSON logs")
    print("=" * 60)


def main():
    # Setup logging with rotation (72 hours)
    log_dir = Path("logs")
    setup_logging(log_dir=log_dir, level="DEBUG", json_logs=True)

    # Run test
    asyncio.run(test_all_alert_levels())


if __name__ == "__main__":
    main()
