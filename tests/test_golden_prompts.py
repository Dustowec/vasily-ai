"""Test Golden Prompts Library."""

from pathlib import Path

from core.golden_prompts import GoldenPromptsLibrary
from core.logging_config import setup_logging


def main():
    setup_logging(Path("logs"), level="DEBUG")

    library = GoldenPromptsLibrary("data/prompts/test_golden.json")

    print(f"Loaded {len(library)} prompts:\n")

    # List all prompts
    for prompt_info in library.list_prompts():
        print(f"- {prompt_info['name']}: {prompt_info['display_name']}")
        print(f"  {prompt_info['description']}\n")

    # Get specific prompt
    search_prompt = library.get_prompt("search")
    print(f"Search prompt preview:\n{search_prompt[:200]}...\n")

    # Test custom prompt
    library.add_prompt(
        name="custom_test",
        display_name="Custom Test",
        description="A test prompt",
        prompt="You are a test assistant.",
    )
    print(f"After adding custom: {len(library)} prompts")

    # Cleanup
    Path("data/prompts/test_golden.json").unlink(missing_ok=True)
    print("\nGolden Prompts test complete!")


if __name__ == "__main__":
    main()
