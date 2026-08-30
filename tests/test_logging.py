"""
Test script for 4-level structured logging.
Run: python test_logging.py
"""

import asyncio
from pathlib import Path

import structlog

from core.logging_config import get_logger, setup_logging


async def simulate_full_request():
    """Simulate a full request flow with all 4 log levels."""
    # Bind request_id to context
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-abc-123")

    # 1. CORE LOGS (AgentCore, PluginRegistry)
    core_logger = get_logger("core", "AgentCore")
    core_logger.info("Agent started", plugins_count=4, version="0.1.0")
    core_logger.debug("PluginRegistry initialized", plugins=["danbooru", "web_search"])

    # 2. INTERACTION LOGS (core-to-plugin calls)
    interaction_logger = get_logger("interaction", "AgentCore")
    interaction_logger.info(
        "Calling plugin",
        plugin="danbooru",
        action="parse_tags",
        params={"query": "1girl", "limit": 10},
    )

    # 3. PLUGINS LOGS (plugin internals)
    plugin_logger = get_logger("plugins", "DanbooruTool")
    plugin_logger.info("Parsing Danbooru", url="https://danbooru.donmai.us/tags.json")
    plugin_logger.debug("HTTP request sent", method="GET", timeout=10)
    plugin_logger.info("Parsing complete", results_count=10, duration_ms=450)

    # Back to interaction logger
    interaction_logger.info(
        "Plugin returned",
        plugin="danbooru",
        status="success",
        duration_ms=450,
    )

    # 4. LLM LOGS (requests/responses)
    llm_logger = get_logger("llm", "OllamaClient")
    llm_logger.info(
        "LLM request",
        model="llama3.2",
        prompt_length=150,
        max_tokens=2048,
    )
    llm_logger.debug(
        "LLM prompt",
        prompt="Generate tags for: 1girl, cyberpunk city...",
    )
    llm_logger.info(
        "LLM response",
        model="llama3.2",
        response_length=320,
        duration_ms=2500,
    )

    # Core logs completion
    core_logger.info("Request completed", total_duration_ms=3200, status="success")

    print("\n" + "=" * 60)
    print("Log files created:")
    print("  - logs/core.log        (AgentCore, PluginRegistry)")
    print("  - logs/interaction.log (core-to-plugin calls)")
    print("  - logs/plugins.log     (plugin internals)")
    print("  - logs/llm.log         (LLM requests/responses)")
    print("  - logs/vasily.log      (all logs combined)")
    print("=" * 60)


def main():
    # Setup logging
    log_dir = Path("logs")
    setup_logging(log_dir=log_dir, level="DEBUG", json_logs=True)

    # Run test
    asyncio.run(simulate_full_request())


if __name__ == "__main__":
    main()
