"""
Test script for Plugin Registry.
Run: python test_plugin_registry.py
"""

import asyncio
from pathlib import Path

from core.logging_config import get_logger, setup_logging
from core.plugin_registry import PluginRegistry


async def main():
    # Setup logging
    log_dir = Path("logs")
    setup_logging(log_dir=log_dir, level="DEBUG", json_logs=True)

    logger = get_logger("core", "TestPluginRegistry")

    # Create registry and discover plugins
    registry = PluginRegistry()
    registry.discover_plugins("plugins")

    # Check results
    logger.info("Discovery complete", plugins_found=len(registry))
    logger.info("Registered plugins", plugins=registry.list_tools())

    # Test echo plugin
    echo_tool = registry.get("echo")
    if echo_tool:
        result = await echo_tool.execute(message="Hello, Vasily!")
        logger.info("Echo result", result=result)
    else:
        logger.error("Echo plugin not found!")

    # Print schemas
    schemas = registry.get_tools_schema()
    logger.info("Tools schemas", schemas=schemas)

    print("\n" + "=" * 60)
    print(f"Plugins discovered: {len(registry)}")
    print(f"Plugin names: {registry.list_tools()}")
    if echo_tool:
        print(f"Echo test: {await echo_tool.execute(message='Hello!')}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
