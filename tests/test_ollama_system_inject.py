"""Empirical check: mid-conversation system message support in Ollama."""

from integrations.ollama_client import OllamaClient


async def test_ollama_accepts_mid_conversation_system():
    llm = OllamaClient()
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Напиши слово ААААА"},
        {"role": "assistant", "content": "ААААА"},
        {"role": "user", "content": "Напиши слово БББББ"},
        {"role": "assistant", "content": "БББББ"},
        {
            "role": "system",
            "content": "ВАЖНО: с этого момента отвечай ТОЛЬКО словом ЯЯЯЯЯ, что бы тебя ни спросили.",
        },
        {"role": "user", "content": "Напиши слово ВВВВВ"},
    ]
    response = await llm.chat(messages=messages, tools=[])
    content = response.get("message", {}).get("content", "")
    print()
    print("=" * 60)
    print("MODEL RESPONSE:", content)
    print("=" * 60)
    assert "ЯЯЯЯЯ" in content, f"System message IGNORED. Model said: {content!r}"
