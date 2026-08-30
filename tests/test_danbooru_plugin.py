"""Test Danbooru plugin."""

import asyncio
from pathlib import Path

from core.logging_config import setup_logging
from core.plugin_registry import PluginRegistry


async def main():
    setup_logging(Path("logs"), level="DEBUG")

    registry = PluginRegistry()
    registry.discover_plugins("plugins")

    print(f"Plugins found: {registry.list_tools()}")

    danbooru = registry.get("danbooru_search")
    if danbooru:
        result = await danbooru.execute(query="1girl", limit=3)
        print(f"Status: {result['status']}")
        print(f"Results: {result.get('results_count', 0)}")
    else:
        print("ERROR: danbooru_search not found!")


if __name__ == "__main__":
    asyncio.run(main())
