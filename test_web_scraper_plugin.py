"""Test Web Scraper plugin."""

import asyncio
from pathlib import Path

from core.logging_config import setup_logging
from core.plugin_registry import PluginRegistry


async def main():
    setup_logging(Path("logs"), level="DEBUG")

    registry = PluginRegistry()
    registry.discover_plugins("plugins")

    print(f"Plugins found: {registry.list_tools()}")

    scraper = registry.get("web_scraper")
    if scraper:
        result = await scraper.execute(url="https://example.com")
        print(f"Status: {result['status']}")
        print(f"Source: {result.get('source', 'unknown')}")
        print(f"Title: {result.get('title', 'N/A')}")
    else:
        print("ERROR: web_scraper not found!")


if __name__ == "__main__":
    asyncio.run(main())
