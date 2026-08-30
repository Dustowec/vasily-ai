"""Test Art Generator plugin."""

import asyncio
from pathlib import Path

from core.logging_config import setup_logging
from core.plugin_registry import PluginRegistry


async def main():
    setup_logging(Path("logs"), level="DEBUG")

    registry = PluginRegistry()
    registry.discover_plugins("plugins")

    print(f"Plugins found: {registry.list_tools()}")

    generator = registry.get("art_generator")
    if generator:
        result = await generator.execute(
            subject="1girl, cyberpunk city, neon lights",
            style="anime style",
            tags=["rain", "night"],
        )
        print(f"Status: {result['status']}")
        print(f"Prompt: {result.get('prompt', 'N/A')}")
        print(f"Negative: {result.get('negative_prompt', 'N/A')[:50]}...")
    else:
        print("ERROR: art_generator not found!")


if __name__ == "__main__":
    asyncio.run(main())
