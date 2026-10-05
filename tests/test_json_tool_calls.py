"""Тесты JSON-fallback для tool calling (ADR-016, вариант A).

Для моделей без нативной поддержки tools в Ollama (например, gemma3n):
- инструкция со схемой инструментов вставляется в system prompt;
- ответ LLM парсится на предмет <tool_call>{...}</tool_call>;
- синтезируется message.tool_calls в формате Ollama;
- ReActLoop видит тот же формат, что и в native-режиме.

На старой логике (без fallback) все тесты падают с AttributeError
на отсутствующие методы.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.ollama_client import OllamaClient

# ==================== Хелпер: мок HTTP-сессии с context manager ====================


def _mock_session_with_json(json_data, status=200):
    """Собрать мок aiohttp-сессии, которая отдаёт указанный JSON.

    Возвращает AsyncMock-сессию, пригодную для session.get(url) в
    конструкции `async with session.get(url) as response`.

    Почему так сложно:
      - session.get — синхронный метод, возвращающий context manager,
        поэтому это MagicMock, а не AsyncMock;
      - сам context manager имеет асинхронные __aenter__ и __aexit__;
      - response.json() — корутина, поэтому AsyncMock.
    """
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=None)

    session = AsyncMock()
    session.get = MagicMock(return_value=cm)
    return session


# ==================== Чистые функции: парсер ====================


@pytest.mark.parametrize(
    "text, expected_count, expected_names",
    [
        # одиночный вызов с тегом
        (
            '<tool_call>{"name": "web_search", "arguments": {"query": "test"}}</tool_call>',
            1,
            ["web_search"],
        ),
        # два вызова подряд
        (
            '<tool_call>{"name": "a", "arguments": {}}</tool_call>'
            '<tool_call>{"name": "b", "arguments": {"x": 1}}</tool_call>',
            2,
            ["a", "b"],
        ),
        # голый JSON без тега (страховка)
        (
            '{"name": "remember_fact", "arguments": {"fact": "x"}}',
            1,
            ["remember_fact"],
        ),
        # markdown-обёртка
        (
            'Вот результат:\n```json\n{"name": "web_search", "arguments": {"query": "y"}}\n```',
            1,
            ["web_search"],
        ),
        # текст + JSON в середине
        (
            'Сейчас вызову: {"name": "x", "arguments": {}} и подожду.',
            1,
            ["x"],
        ),
    ],
)
def test_parse_tool_calls(text, expected_count, expected_names):
    """Парсер извлекает tool_calls из разных форматов ответа."""
    calls = OllamaClient._parse_tool_calls_from_text(text)
    assert len(calls) == expected_count
    assert [c["function"]["name"] for c in calls] == expected_names


def test_parse_returns_empty_on_broken_json():
    """Битый JSON — не выполняем ничего, безопасный дефолт."""
    calls = OllamaClient._parse_tool_calls_from_text("<tool_call>{broken json}</tool_call>")
    assert calls == []


def test_parse_returns_empty_on_plain_text():
    """Обычный текст без вызовов — пустой список."""
    calls = OllamaClient._parse_tool_calls_from_text("Привет, как дела?")
    assert calls == []


def test_parse_returns_empty_on_empty_string():
    """Пустой ответ — пустой список."""
    assert OllamaClient._parse_tool_calls_from_text("") == []
    assert OllamaClient._parse_tool_calls_from_text(None) == []


def test_parse_arguments_preserved_as_dict():
    """Arguments приходят dict'ом, вложенные структуры сохраняются."""
    calls = OllamaClient._parse_tool_calls_from_text(
        '<tool_call>{"name": "x", "arguments": {"nested": {"a": [1, 2]}}}</tool_call>'
    )
    assert calls[0]["function"]["arguments"] == {"nested": {"a": [1, 2]}}


def test_parse_arguments_as_json_string():
    """Если LLM обернула arguments в строку — распарсить."""
    calls = OllamaClient._parse_tool_calls_from_text(
        '<tool_call>{"name": "x", "arguments": "{\\"a\\": 1}"}</tool_call>'
    )
    assert calls[0]["function"]["arguments"] == {"a": 1}


# ==================== Инструкция для system prompt ====================


def _sample_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "limit": {"type": "integer", "description": "Max results"},
                    },
                    "required": ["query"],
                },
            },
        }
    ]


def test_build_tools_instruction_includes_tool_names():
    """Все имена инструментов попадают в инструкцию."""
    text = OllamaClient._build_tools_instruction(_sample_tools())
    assert "web_search" in text
    assert "Search the web" in text


def test_build_tools_instruction_marks_required_params():
    """Required-параметры отмечены явно."""
    text = OllamaClient._build_tools_instruction(_sample_tools())
    assert "query" in text
    assert "required" in text.lower()


def test_build_tools_instruction_has_format_example():
    """В инструкции есть пример формата <tool_call>."""
    text = OllamaClient._build_tools_instruction(_sample_tools())
    assert "<tool_call>" in text
    assert "</tool_call>" in text


def test_build_tools_instruction_empty_tools():
    """Пустой список tools — пустая строка или минимальная инструкция."""
    text = OllamaClient._build_tools_instruction([])
    assert isinstance(text, str)


# ==================== Capabilities detection ====================


async def test_detect_capabilities_native():
    """Модель с tools в capabilities → native-режим."""
    client = OllamaClient(model="qwen-with-tools")

    session = _mock_session_with_json(
        {
            "models": [
                {"name": "qwen-with-tools", "capabilities": ["tools", "completion"]},
            ]
        }
    )

    with patch.object(client, "_get_session", AsyncMock(return_value=session)):
        result = await client._detect_tools_support()

    assert result is True
    assert client._tools_supported is True


async def test_detect_capabilities_fallback():
    """Модель без tools → fallback-режим."""
    client = OllamaClient(model="gemma-no-tools")

    session = _mock_session_with_json(
        {
            "models": [
                {"name": "gemma-no-tools", "capabilities": ["completion"]},
            ]
        }
    )

    with patch.object(client, "_get_session", AsyncMock(return_value=session)):
        result = await client._detect_tools_support()

    assert result is False
    assert client._tools_supported is False


async def test_detect_capabilities_missing_model_defaults_fallback():
    """Модель не в списке → безопасный дефолт = fallback."""
    client = OllamaClient(model="unknown-model")

    session = _mock_session_with_json({"models": []})

    with patch.object(client, "_get_session", AsyncMock(return_value=session)):
        result = await client._detect_tools_support()

    assert result is False


async def test_detect_capabilities_caches_result():
    """Повторный вызов не дёргает API — используется кэш."""
    client = OllamaClient(model="cached")
    client._tools_supported = True  # уже закэшировано

    with patch.object(client, "_get_session") as mock_session:
        result = await client._detect_tools_support()
        mock_session.assert_not_called()

    assert result is True


# ==================== Интеграция chat() ====================


async def test_chat_fallback_injects_instruction_into_system():
    """В fallback-режиме инструкция добавляется в system prompt."""
    client = OllamaClient(model="gemma-no-tools")
    client._tools_supported = False  # уже знаем — fallback

    captured_payload = {}

    async def fake_request(endpoint, payload):
        captured_payload.update(payload)
        return {"message": {"content": "Просто ответ"}}

    with patch.object(client, "_request_with_retries", side_effect=fake_request):
        await client.chat(
            messages=[{"role": "system", "content": "Base prompt"}],
            tools=_sample_tools(),
        )

    # Ключевое: tools НЕ переданы в payload (fallback не использует их),
    # а инструкция — в system message
    assert "tools" not in captured_payload
    system_msg = captured_payload["messages"][0]
    assert system_msg["role"] == "system"
    assert "Base prompt" in system_msg["content"]
    assert "<tool_call>" in system_msg["content"]
    assert "web_search" in system_msg["content"]


async def test_chat_fallback_synthesizes_tool_calls():
    """В fallback ответ LLM с <tool_call> превращается в message.tool_calls."""
    client = OllamaClient(model="gemma-no-tools")
    client._tools_supported = False

    async def fake_request(endpoint, payload):
        return {
            "message": {
                "content": '<tool_call>{"name": "web_search", "arguments": {"query": "test"}}</tool_call>'
            }
        }

    with patch.object(client, "_request_with_retries", side_effect=fake_request):
        result = await client.chat(
            messages=[{"role": "user", "content": "поищи"}],
            tools=_sample_tools(),
        )

    # Ключевое: tool_calls синтезированы в формате Ollama.
    # ReActLoop увидит их как родные.
    tool_calls = result["message"]["tool_calls"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "web_search"
    assert tool_calls[0]["function"]["arguments"] == {"query": "test"}


async def test_chat_fallback_strips_tool_call_tags_from_content():
    """Теги <tool_call> не остаются в content — иначе логика ниже запутается."""
    client = OllamaClient(model="gemma-no-tools")
    client._tools_supported = False

    async def fake_request(endpoint, payload):
        return {"message": {"content": '<tool_call>{"name": "x", "arguments": {}}</tool_call>'}}

    with patch.object(client, "_request_with_retries", side_effect=fake_request):
        result = await client.chat(messages=[], tools=_sample_tools())

    content = result["message"]["content"]
    assert "<tool_call>" not in content
    assert "</tool_call>" not in content


async def test_chat_native_does_not_inject_instruction():
    """В native-режиме всё работает по-старому, без injection."""
    client = OllamaClient(model="qwen-with-tools")
    client._tools_supported = True

    captured_payload = {}

    async def fake_request(endpoint, payload):
        captured_payload.update(payload)
        return {"message": {"content": "Ответ"}}

    with patch.object(client, "_request_with_retries", side_effect=fake_request):
        await client.chat(
            messages=[{"role": "system", "content": "Base"}],
            tools=_sample_tools(),
        )

    # В native: tools переданы явно, инструкция НЕ добавлена
    assert "tools" in captured_payload
    assert captured_payload["tools"] == _sample_tools()
    system_msg = captured_payload["messages"][0]
    assert system_msg["content"] == "Base"  # не изменён


async def test_chat_without_tools_skips_fallback():
    """tools=None или tools=[] — никакого fallback, обычный запрос."""
    client = OllamaClient(model="whatever")

    captured_payload = {}

    async def fake_request(endpoint, payload):
        captured_payload.update(payload)
        return {"message": {"content": "Ответ"}}

    with patch.object(client, "_request_with_retries", side_effect=fake_request):
        await client.chat(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert "tools" not in captured_payload
    # system message не создан, потому что его не было
    assert captured_payload["messages"][0]["role"] == "user"
