"""Тесты нормализации истории для fallback-режима (ADR-016, P0-фикс).

Проблема: ReActLoop кладёт результаты инструментов как {"role": "tool"}.
В native-режиме Ollama понимает это. В fallback (модель без native
tools) Ollama пытается построить парсер tools по TEMPLATE и падает
с HTTP 400 "Unable to generate parser for this template".

Решение: в fallback клиент нормализует messages перед отправкой —
assistant.tool_calls → assistant с текстом <tool_call>, role:tool →
role:user с префиксом. ReActLoop не меняется.
"""

from unittest.mock import patch

from integrations.ollama_client import OllamaClient

# ==================== _normalize_history_for_fallback ====================


def test_normalize_assistant_with_tool_calls_to_text():
    """assistant с tool_calls → assistant с текстом <tool_call>."""
    messages = [
        {"role": "system", "content": "Base"},
        {"role": "user", "content": "поищи"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "web_search",
                        "arguments": {"query": "test"},
                    }
                }
            ],
        },
    ]

    result = OllamaClient._normalize_history_for_fallback(messages)

    assert len(result) == 3
    assistant_msg = result[2]
    assert assistant_msg["role"] == "assistant"
    # блок: tool_calls удалены — Ollama не должна их видеть в fallback.
    assert "tool_calls" not in assistant_msg
    # блок: вызов восстановлен как текст <tool_call>.
    assert "<tool_call>" in assistant_msg["content"]
    assert "web_search" in assistant_msg["content"]


def test_normalize_tool_role_to_user():
    """role:tool → role:user с префиксом "[Результат инструмента]:"."""
    messages = [
        {"role": "user", "content": "поищи"},
        {"role": "tool", "content": '{"status": "success", "data": "..."}'},
    ]

    result = OllamaClient._normalize_history_for_fallback(messages)

    assert len(result) == 2
    tool_msg = result[1]
    assert tool_msg["role"] == "user"
    assert "[Результат инструмента]" in tool_msg["content"]
    assert '{"status": "success"' in tool_msg["content"]


def test_normalize_preserves_plain_messages():
    """Обычные user/assistant/system остаются нетронутыми."""
    messages = [
        {"role": "system", "content": "Base"},
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "привет!"},
    ]

    result = OllamaClient._normalize_history_for_fallback(messages)

    assert result == messages
    # блок: возвращена копия, не тот же объект.
    assert result is not messages


def test_normalize_does_not_mutate_input():
    """Исходный список не мутируется — ReActLoop держит его между итерациями."""
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "x", "arguments": {}}}],
        },
    ]
    original_copy = [dict(m) for m in messages]

    OllamaClient._normalize_history_for_fallback(messages)

    assert messages == original_copy


def test_normalize_empty_messages():
    """Пустой список → пустой список."""
    assert OllamaClient._normalize_history_for_fallback([]) == []


def test_normalize_assistant_without_tool_calls_unchanged():
    """assistant без tool_calls — обычное сообщение, не трогаем."""
    messages = [
        {"role": "assistant", "content": "финальный ответ"},
    ]
    result = OllamaClient._normalize_history_for_fallback(messages)
    assert result[0]["content"] == "финальный ответ"
    assert "tool_calls" not in result[0]


# ==================== Интеграция через chat() ====================


async def test_chat_fallback_normalizes_history_before_sending():
    """chat() в fallback нормализует messages перед отправкой в Ollama."""
    client = OllamaClient(model="gemma-no-tools")
    client._tools_supported = False

    captured_payload = {}

    async def fake_request(endpoint, payload):
        captured_payload.update(payload)
        return {"message": {"content": "ок"}}

    with patch.object(client, "_request_with_retries", side_effect=fake_request):
        await client.chat(
            messages=[
                {"role": "user", "content": "запомни"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "remember_fact", "arguments": {"fact": "x"}}}
                    ],
                },
                {"role": "tool", "content": '{"status": "success"}'},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "remember_fact",
                        "description": "Save",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                }
            ],
        )

    # Ключевое: в payload нет ни role:tool, ни tool_calls.
    roles = [m["role"] for m in captured_payload["messages"]]
    assert "tool" not in roles, f"role:tool не должен попадать в payload, роли: {roles}"
    for m in captured_payload["messages"]:
        assert "tool_calls" not in m, f"tool_calls не должен попадать в payload: {m}"


async def test_chat_native_keeps_tool_calls_and_role_tool():
    """В native-режиме нормализация НЕ применяется — tools работают как есть."""
    client = OllamaClient(model="qwen-with-tools")
    client._tools_supported = True

    captured_payload = {}

    async def fake_request(endpoint, payload):
        captured_payload.update(payload)
        return {"message": {"content": "ок"}}

    with patch.object(client, "_request_with_retries", side_effect=fake_request):
        await client.chat(
            messages=[
                {"role": "user", "content": "запомни"},
                {"role": "tool", "content": "result"},
            ],
            tools=[
                {"type": "function", "function": {"name": "x", "description": "", "parameters": {}}}
            ],
        )

    # В native: role:tool остаётся на месте — Ollama ожидает этого.
    roles = [m["role"] for m in captured_payload["messages"]]
    assert "tool" in roles
