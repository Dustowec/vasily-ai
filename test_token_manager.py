"""Test TokenManager."""

from core.token_manager import TokenManager


def main():
    manager = TokenManager(max_tokens=32768)

    # Test estimation
    short_text = "Hello world"
    print(f"'{short_text}' ≈ {manager.estimate_tokens(short_text)} tokens")

    long_text = "A" * 10000
    print(f"10k chars ≈ {manager.estimate_tokens(long_text)} tokens")

    # Test message counting
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is 2+2?"},
        {"role": "assistant", "content": "4"},
    ]
    total = manager.count_messages_tokens(messages)
    print(f"\n3 messages total: {total} tokens")

    # Test trimming
    print("\n=== Testing trim ===")
    large_messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "First question" + "A" * 50000},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Second question"},
    ]
    print(f"Before trim: {manager.count_messages_tokens(large_messages)} tokens")

    trimmed = manager.trim_messages(large_messages)
    print(f"After trim: {manager.count_messages_tokens(trimmed)} tokens")
    print(f"Messages count: {len(large_messages)} → {len(trimmed)}")

    # Test usage report
    print("\n=== Usage report ===")
    report = manager.get_usage_report(messages)
    print(f"Used: {report['used_tokens']} / {report['max_tokens']}")
    print(f"Usage: {report['usage_percent']}%")

    print("\nTokenManager test complete!")


if __name__ == "__main__":
    main()
