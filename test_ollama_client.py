"""Test OllamaClient."""

import asyncio
from pathlib import Path

import structlog

from core.logging_config import setup_logging
from integrations.ollama_client import LLMUnavailableError, OllamaClient


async def main():
    setup_logging(Path("logs"), level="DEBUG")

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="test-ollama-001")

    client = OllamaClient()

    print("=== Health Check ===")
    available = await client.health_check()
    print(f"Ollama available: {available}")

    if available:
        print("\n=== Chat Test ===")
        try:
            response = await client.chat(
                messages=[{"role": "user", "content": "Say hello in one word."}]
            )
            content = response.get("message", {}).get("content", "")
            print(f"Chat response: {content[:100]}")
        except LLMUnavailableError as e:
            print(f"LLM unavailable: {e}")

        print("\n=== Generate Test ===")
        try:
            response = await client.generate("What is 2+2? Answer with just the number.")
            print(f"Generate response: {response.get('response', '')[:100]}")
        except LLMUnavailableError as e:
            print(f"LLM unavailable: {e}")
    else:
        print("Ollama not running. Start it with: ollama serve")

    await client.close()
    print("\nOllamaClient test complete!")


if __name__ == "__main__":
    asyncio.run(main())
