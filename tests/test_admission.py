"""Тесты admission gate (Шаг 3 Задачи №2).

Три уровня проверки:
  1. Юнит: check_admission — что ловим и что пропускаем.
  2. Интеграция: manager реально блокирует запись.
  3. Инструмент: RememberFactTool возвращает status="rejected".

На старой логике (без gate) все интеграционные тесты красные.
"""

import pytest

from core.internal_tools import RememberFactTool
from memory.admission import check_admission
from memory.manager import GradientMemory


# ==================== Юнит: check_admission ====================


@pytest.mark.parametrize(
    "value",
    [
        "мой пароль: hunter2",  # "пароль: ..."
        "password: hunter2",  # "password: ..."
        "PASSWORD = hunter2",  # регистр + другой разделитель
        "api_key: abcdef1234567890",  # api_key
        "api-key: abcdef1234567890",  # другой разделитель
        "token: xyz123",  # token
        "мой токен: abcdef",  # русский "токен"
    ],
)
def test_secret_key_pairs_rejected(value):
    """Пары 'секретный_ключ: значение' блокируются."""
    assert check_admission(value) == "secret_key_pair"


@pytest.mark.parametrize(
    "value",
    [
        "Bearer eyJhbGciOiJIUzI1NiJ9",  # JWT: есть цифры
        "bearer abc123def456",  # OAuth: есть цифры
        "BEARER aVeryLongTokenWithoutDigitsOnlyLettersHere",  # 20+ символов
    ],
)
def test_bearer_tokens_rejected(value):
    """Bearer <token> блокируется, если токен похож на токен.

    Критерий: содержит цифры ИЛИ длина 20+. Отдельный тест,
    потому что Bearer — не пара 'ключ: значение', а конструкция
    через пробел.
    """
    assert check_admission(value) == "bearer_token"


@pytest.mark.parametrize(
    "value",
    [
        "я использую Bearer authentication",  # слово, не токен
        "Bearer XYZ",  # короткое, без цифр
        "Bearer test",  # очевидно не токен
    ],
)
def test_bearer_word_alone_passes(value):
    """Bearer + обычное слово НЕ блокируется.

    False positive из первой реализации: regex ловил ЛЮБОЕ слово
    после Bearer. Настоящий токен содержит цифры или длинный.
    """
    assert check_admission(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "sk-" + "a" * 30,  # OpenAI
        "ghp_" + "b" * 35,  # GitHub personal
        "gho_" + "c" * 35,  # GitHub oauth
        "xoxb-" + "d" * 25,  # Slack
        "AIza" + "e" * 35,  # Google
    ],
)
def test_token_prefixes_rejected(value):
    """Токены с узнаваемым префиксом блокируются."""
    assert check_admission(value) == "token_prefix"


def test_long_random_string_rejected():
    """Длинная base64-подобная строка блокируется."""
    value = "aB3dE5gH7jK9lM2nP4qR6sT8vW0xY1zA2bC4dE6fG8"
    assert check_admission(value) == "long_random_string"


@pytest.mark.parametrize(
    "value",
    [
        "меня зовут Вася",
        "кот спит на подоконнике",
        "я люблю кофе с молоком",
        "у меня день рождения 15 июня",
        "живу в Москве",
        "работаю программистом",
        "сегодня была хорошая погода",
        "",
        None,
        42,
        3.14,
        "пароль",  # одно слово без значения — не секрет
        "я забыл свой пароль",  # фраза, но без ':' или '=' — пропуск
        "x" * 200,  # монотонная заглушка — не секрет
        "aaa" * 50,  # то же самое, другой символ
    ],
)
def test_normal_values_pass(value):
    """Обычные факты НЕ блокируются."""
    assert check_admission(value) is None


# ==================== Интеграция: manager ====================


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер: файлы памяти во временной папке."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


async def test_remember_user_fact_rejects_secret(m):
    """Секрет не попадает в память, возвращается причина."""
    result = await m.remember_user_fact("user_fact:leak", "мой пароль: hunter2")
    assert result["stored"] is False
    assert result["reason"] == "secret_key_pair"
    # блок: ни в одной зоне нет записи.
    # почему: gate срабатывает ДО взятия lock — никаких side effects.
    assert "user_fact:leak" not in m._hot
    assert "user_fact:leak" not in m._tgs
    assert "user_fact:leak" not in m._cold


async def test_remember_user_fact_accepts_normal(m):
    """Обычный факт записывается успешно, контракт соблюдён."""
    result = await m.remember_user_fact("user_fact:ok", "меня зовут Вася")
    assert result["stored"] is True
    assert result["key"] == "user_fact:ok"
    assert "user_fact:ok" in m._hot


async def test_remember_dialogue_summary_rejects_secret(m):
    """Диалоговое саммари с секретом не записывается."""
    result = await m.remember_dialogue_summary(
        "dialogue_summary:x", {"summary": "token: abc123secret"}
    )
    assert result["stored"] is False
    assert result["reason"] == "secret_key_pair"
    assert "dialogue_summary:x" not in m._hot


async def test_remember_generic_rejects_secret(m):
    """Общий remember() тоже уважает gate."""
    result = await m.remember("plain_key", "sk-" + "x" * 30)
    assert result["stored"] is False
    assert result["reason"] == "token_prefix"
    assert "plain_key" not in m._hot


# ==================== Инструмент: RememberFactTool ====================


async def test_remember_fact_tool_returns_rejected_status(m):
    """RememberFactTool возвращает status='rejected' для секрета.

    Без llm_client similarity-verdict возвращает 'НЕТ' — пропускаем
    dedup-ветки и сразу идём в "новая запись". Там gate отклоняет.
    """
    tool = RememberFactTool(memory_manager=m, llm_client=None)
    result = await tool._execute(fact="мой пароль: hunter2")
    assert result["status"] == "rejected"
    assert result["reason"] == "secret_key_pair"
    assert (
        "rejected" in result["message"].lower()
        or "не сохранил" in result["message"].lower()
    )


async def test_remember_fact_tool_accepts_normal(m):
    """Обычный факт проходит через инструмент без ошибок."""
    tool = RememberFactTool(memory_manager=m, llm_client=None)
    result = await tool._execute(fact="меня зовут Вася")
    assert result["status"] == "success"
    assert result["key"].startswith("user_fact:")
