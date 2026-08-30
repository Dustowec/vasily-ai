"""Test AgentCore initialization and request handling."""

import asyncio
from pathlib import Path

from core.agent import AgentCore
from core.config import Config
from core.logging_config import setup_logging


async def main():
    setup_logging(Path("logs"), level="DEBUG")

    config = Config.load()
    agent = AgentCore(config)

    # Initialize
    await agent.initialize()

    # Test request handling
    response = await agent.handle_request({"id": "test-001", "text": "Hello"})
    print(f"\nResponse: {response}")

    # Test metrics
    metrics = agent.get_metrics()
    print(f"\nMetrics: {metrics}")

    # Test error isolation
    response_err = await agent.handle_request({"id": "test-002", "text": None})
    print(f"\nError response handled: {response_err}")

    # Shutdown
    await agent.shutdown()

    print("\nAgentCore test complete!")


if __name__ == "__main__":
    asyncio.run(main())
