"""Test LLM compressor."""

import asyncio
from pathlib import Path

from core.logging_config import setup_logging
from integrations.ollama_client import OllamaClient
from memory.llm_compressor import LLMCompressor


async def main():
    setup_logging(Path("logs"), level="DEBUG")

    client = OllamaClient()
    compressor = LLMCompressor(client)

    # Test 1: Short text (no compression needed)
    short = "Hello world"
    result = await compressor.compress(short)
    print(f"Short text: '{short}' → '{result}'")

    # Test 2: Long text (should be summarized)
    long_text = (
        "The quick brown fox jumps over the lazy dog. "
        "This is a test of the memory compression system. "
        "The agent needs to remember important information between sessions. "
        "LLM-powered compression creates summaries instead of just truncating text. "
        "This allows the agent to retain key facts while reducing storage size. "
        "The compression happens in the background every 6 hours. "
        "Only expired entries from hot memory are compressed and moved to cold storage. "
        "This two-tier system balances speed and capacity."
    )
    result = await compressor.compress(long_text)
    print(f"\nLong text ({len(long_text)} chars) → Summary ({len(result)} chars):")
    print(f"'{result}'")

    # Test 3: Dictionary
    dict_value = {
        "user_name": "Alex",
        "session_count": 42,
        "last_query": "cyberpunk art generation",
        "preferences": ["anime", "detailed", "high quality"],
    }
    result = await compressor.compress(dict_value)
    print(f"\nDictionary → Summary: '{result}'")

    # Test 4: List
    list_value = ["item1", "item2", "item3", "item4", "item5"]
    result = await compressor.compress(list_value)
    print(f"\nList → Summary: '{result}'")

    await client.close()
    print("\nLLM compressor test complete!")


if __name__ == "__main__":
    asyncio.run(main())
