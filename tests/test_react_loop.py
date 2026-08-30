"""Test ReAct loop with real LLM and plugins."""

import asyncio
from pathlib import Path

import structlog

from core.config import Config
from core.logging_config import setup_logging
from core.plugin_registry import PluginRegistry
from core.react_loop import ReActLoop
from integrations.ollama_client import OllamaClient


async def main():
    setup_logging(Path("logs"), level="DEBUG")

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="test-react-001")

    config = Config.load()

    registry = PluginRegistry()
    registry.discover_plugins(config.plugins_dir)

    client = OllamaClient()
    try:
        loop = ReActLoop(config=config, llm_client=client, plugin_registry=registry)

        result = await loop.run(
            "Use the echo tool with message 'hello from ReAct' and tell me what it returned."
        )

        print(f"\nStatus: {result['status']}")
        print(f"Answer: {result['answer'][:300]}")
        print(f"Iterations: {result['iterations']}")
        print(f"Tool steps: {len(result['steps'])}")
        for step in result["steps"]:
            print(f"  - {step['tool']}: {step['result_preview']}")
    finally:
        await client.close()

    print("\nReAct test complete!")


if __name__ == "__main__":
    asyncio.run(main())
