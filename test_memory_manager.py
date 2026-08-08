"""Test MemoryManager."""

from pathlib import Path

from core.logging_config import setup_logging
from memory.manager import MemoryManager


def main():
    setup_logging(Path("logs"), level="DEBUG")

    memory = MemoryManager()

    memory.remember("user_name", "Alex")
    memory.remember("last_query", "cyberpunk girl")
    memory.remember("session_data", {"count": 5, "duration": "10m"})

    print(f"User: {memory.recall('user_name')}")
    print(f"Query: {memory.recall('last_query')}")

    context = memory.build_context("cyberpunk art generation")
    print(f"\nRAG Context:\n{context}")

    # Test compression
    def simple_compressor(value):
        text = str(value)
        return text[:100] + "..." if len(text) > 100 else text

    compressed = memory.compress_to_cold("session_data", simple_compressor)
    print(f"\nCompressed session_data: {compressed}")
    print(f"Cold recall: {memory.recall('session_data')}")

    print("\nMemoryManager test complete!")


if __name__ == "__main__":
    main()
