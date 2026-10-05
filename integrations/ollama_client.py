"""OllamaClient - async LLM client with retries and crash reporting.

Stage 5a: num_predict passthrough, token drift calibration, honest
timeout handling, unclosed-think handling.
Stage 6.2b: third thinking case — CLOSING </think> without an opener.
ADR-016: JSON tool-call fallback for models without native tools
capability (e.g. gemma3n in Ollama 0.35.x). Client auto-detects
capabilities at first use, chooses native or fallback path, and
returns the SAME message.tool_calls format to ReActLoop.
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import aiohttp

from core.crash_reporter import CrashReporter
from core.logging_config import get_logger

logger = get_logger("llm", "OllamaClient")

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "vasily-qwen"
DEFAULT_TEMPERATURE = 0.1
DEFAULT_TIMEOUT = 120.0
DEFAULT_NUM_CTX = 8192
DEFAULT_NUM_PREDICT = 3072
DEFAULT_RETRY_DELAY_BASE = 1.0
MAX_RETRIES = 2

TOKEN_DRIFT_WARNING_THRESHOLD = 0.15

# ADR-016: теги для JSON-вызовов инструментов (fallback-режим).
# Модель оборачивает вызов в <tool_call>...</tool_call>, парсер
# извлекает JSON и синтезирует message.tool_calls формата Ollama.
TOOL_CALL_OPEN = "<tool_call>"
TOOL_CALL_CLOSE = "</tool_call>"

# ADR-016: регекспы для парсера. Скомпилированы один раз.
_TAG_BLOCK_RE = re.compile(
    re.escape(TOOL_CALL_OPEN) + r"(.*?)" + re.escape(TOOL_CALL_CLOSE),
    re.DOTALL,
)
_MARKDOWN_FENCE_RE = re.compile(r"```(?:json)?", re.IGNORECASE)


class LLMUnavailableError(Exception):
    """Raised when LLM is unavailable after all retries."""


class OllamaClient:
    """Async client for Ollama LLM server."""

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        num_ctx: int = DEFAULT_NUM_CTX,
        num_predict: int = DEFAULT_NUM_PREDICT,
        retry_delay_base: float = DEFAULT_RETRY_DELAY_BASE,
        log_dir: str = "logs",
        token_manager: Any | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.retry_delay_base = retry_delay_base
        self.token_manager = token_manager
        self._session: aiohttp.ClientSession | None = None
        self._crash_reporter = CrashReporter(Path(log_dir))
        # блок: кэш поддержки tools. None = ещё не проверяли.
        # почему: /api/tags надо дёрнуть один раз на модель — потом кэш.
        # True = native, False = fallback (JSON через <tool_call>).
        self._tools_supported: bool | None = None

    # ================== ADR-016: JSON tool-call fallback ==================

    @staticmethod
    def _try_parse_call(blob: str) -> dict | None:
        """Распарсить один JSON-объект вызова инструмента.

        Возвращает dict в формате Ollama tool_call:
            {"function": {"name": ..., "arguments": {...}}}
        или None если JSON битый / нет ключа name.
        """
        blob = blob.strip()
        if not blob:
            return None
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        name = obj.get("name")
        if not name or not isinstance(name, str):
            return None

        # блок: arguments может прийти dict или JSON-строкой.
        # почему: некоторые модели пишут `"arguments": "{\"a\":1}"`,
        # другие — `"arguments": {"a": 1}`. Унифицируем в dict.
        args = obj.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}

        return {"function": {"name": name, "arguments": args}}

    @staticmethod
    def _find_raw_json_calls(text: str) -> list[dict]:
        """Найти сбалансированные {...} блоки без тегов.

        Сканирует текст слева направо, отслеживает баланс фигурных
        скобок, пытается распарсить каждый блок как tool_call.
        """
        calls: list[dict] = []
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start >= 0:
                        blob = text[start : i + 1]
                        parsed = OllamaClient._try_parse_call(blob)
                        if parsed:
                            calls.append(parsed)
                        start = -1
        return calls

    @classmethod
    def _parse_tool_calls_from_text(cls, text: str | None) -> list[dict]:
        """Извлечь tool_calls из текстового ответа модели.

        Приоритет:
          1. Блоки <tool_call>...</tool_call> — если они есть, работаем
             только с ними (не тащим мусор между блоками).
          2. Если тегов нет — ищем «голые» сбалансированные {...}.

        Возвращает список в формате Ollama message.tool_calls.
        """
        if not text:
            return []

        # блок: markdown-обёртки убираем ДО парсинга.
        # почему: ```json ... ``` вокруг JSON ломает сбалансированный
        # скан, потому что backtick'и — не часть JSON.
        cleaned = _MARKDOWN_FENCE_RE.sub(" ", text)

        tag_matches = list(_TAG_BLOCK_RE.finditer(cleaned))
        if tag_matches:
            # блок: работаем только с содержимым тегов.
            # почему: если модель обернула вызов в тег, всё остальное —
            # рассуждения, которые могут содержать случайный JSON.
            calls: list[dict] = []
            for m in tag_matches:
                parsed = cls._try_parse_call(m.group(1))
                if parsed:
                    calls.append(parsed)
            return calls

        return cls._find_raw_json_calls(cleaned)

    @staticmethod
    def _build_tools_instruction(tools: list[dict]) -> str:
        """Собрать текст-инструкцию со схемой инструментов для fallback.

        Вход: tools в формате Ollama ([{"type": "function",
        "function": {"name": ..., "parameters": {...}}}]).
        Выход: markdown-подобный текст для system prompt.
        """
        if not tools:
            return ""

        lines = [
            "У тебя есть доступ к инструментам. Используй их, когда нужно.",
            "",
            "Доступные инструменты:",
        ]
        for tool in tools:
            fn = tool.get("function", {})
            name = fn.get("name", "")
            desc = fn.get("description", "")
            lines.append(f"- {name} — {desc}")

            params = fn.get("parameters", {})
            props = params.get("properties", {})
            required = set(params.get("required", []))
            if props:
                lines.append("  Параметры:")
                for pname, pdef in props.items():
                    ptype = pdef.get("type", "string")
                    pdesc = pdef.get("description", "")
                    marker = " (required)" if pname in required else ""
                    lines.append(f"    {pname} ({ptype}{marker}) — {pdesc}")

        lines.append("")
        lines.append(
            "Чтобы вызвать инструмент, ответь РОВНО в таком формате, " "без лишнего текста:"
        )
        lines.append(
            TOOL_CALL_OPEN
            + '{"name": "имя_инструмента", "arguments": {"параметр": "значение"}}'
            + TOOL_CALL_CLOSE
        )
        lines.append("")
        lines.append(
            "Если инструмент не нужен — ответь обычным текстом, "
            "без тегов " + TOOL_CALL_OPEN + "."
        )
        return "\n".join(lines)

    @staticmethod
    def _inject_tools_instruction(messages: list[dict], tools: list[dict]) -> list[dict]:
        """Вернуть копию messages с инструктией в system prompt.

        Не мутирует исходный список — ReActLoop держит его между итерациями.
        """
        instruction = OllamaClient._build_tools_instruction(tools)
        if not instruction:
            return messages

        new_messages = [dict(m) for m in messages]
        if new_messages and new_messages[0].get("role") == "system":
            new_messages[0]["content"] = new_messages[0].get("content", "") + "\n\n" + instruction
        else:
            # блок: system-сообщения нет — создаём.
            # почему: инструкция про tools обязана быть в system, иначе
            # модель не поймёт формат.
            new_messages.insert(0, {"role": "system", "content": instruction})
        return new_messages

    @staticmethod
    def _apply_fallback_to_result(result: dict) -> None:
        """Мутирует result: парсит tool_calls из content, чистит теги.

        Если вызовы найдены — кладём в message.tool_calls в формате
        Ollama, а из content убираем блоки <tool_call>...</tool_call>.
        """
        message = result.get("message")
        if not isinstance(message, dict):
            return
        content = message.get("content", "")
        calls = OllamaClient._parse_tool_calls_from_text(content)
        if not calls:
            return

        message["tool_calls"] = calls
        # блок: вырезаем теги <tool_call> из content.
        # почему: ReActLoop кладёт content в историю; если там останутся
        # теги, следующая итерация может их «подсмотреть» и повторить.
        message["content"] = _TAG_BLOCK_RE.sub("", content).strip()

    async def _detect_tools_support(self) -> bool:
        """Определить, поддерживает ли модель native tools.

        Дёргает /api/tags один раз, читает capabilities, кэширует в
        self._tools_supported. Модель не найдена / ошибка → False
        (безопасный дефолт — fallback).
        """
        # блок: если уже проверяли — вернуть кэш без HTTP.
        # почему: capabilities модели не меняются в течение сессии.
        if self._tools_supported is not None:
            return self._tools_supported

        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/api/tags") as response:
                if response.status != 200:
                    logger.warning(
                        "Capabilities check failed, fallback to JSON tools",
                        status=response.status,
                    )
                    self._tools_supported = False
                    return False
                data = await response.json()
        except Exception as e:
            logger.warning(
                "Capabilities check error, fallback to JSON tools",
                error=str(e),
            )
            self._tools_supported = False
            return False

        models = data.get("models", []) if isinstance(data, dict) else []
        for m in models:
            # блок: сравниваем по имени без тега :latest.
            # почему: в /api/tags имя приходит с тегом, а self.model
            # может быть без него (или наоборот).
            name = (m.get("name") or "").split(":")[0]
            target = self.model.split(":")[0]
            if name == target:
                caps = m.get("capabilities") or []
                self._tools_supported = "tools" in caps
                logger.info(
                    "Tools support detected",
                    model=self.model,
                    capabilities=caps,
                    native_tools=self._tools_supported,
                )
                return self._tools_supported

        logger.warning(
            "Model not found in /api/tags, fallback to JSON tools",
            model=self.model,
        )
        self._tools_supported = False
        return False

    # ================== Основные методы ==================

    def _build_options(self, **kwargs) -> dict[str, Any]:
        """Build options dict. kwargs override defaults (including num_ctx)."""
        return {
            "temperature": self.temperature,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
            **kwargs,
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def health_check(self) -> bool:
        """Quick check if Ollama is available."""
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.base_url}/api/tags",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as response:
                return response.status == 200
        except Exception:
            return False

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Send chat request to Ollama.

        ADR-016: если модель поддерживает native tools — передаём их
        через payload["tools"]. Если нет — вставляем схему в system prompt
        как текст, парсим <tool_call> из ответа, синтезируем
        message.tool_calls. ReActLoop видит один и тот же формат.
        """
        # блок: ветвление native/fallback только если tools переданы.
        # почему: chat() без tools используется для финального ответа
        # в ReActLoop (tools=[]) и для внутренних вызовов — там
        # никакой обработки не нужно.
        use_fallback = False
        if tools:
            if self._tools_supported is None:
                await self._detect_tools_support()
            if not self._tools_supported:
                use_fallback = True

        if use_fallback:
            # блок: fallback-режим — схема идёт текстом в system.
            # почему: модель не знает про tools, но должна узнать формат
            # из промпта, чтобы написать <tool_call>{...}</tool_call>.
            payload_messages = self._inject_tools_instruction(messages, tools)
            payload = {
                "model": self.model,
                "messages": payload_messages,
                "stream": False,
                "options": self._build_options(**kwargs),
            }
            result = await self._request_with_retries("/api/chat", payload)
            self._apply_fallback_to_result(result)
        else:
            # блок: native-режим (или chat без tools).
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": self._build_options(**kwargs),
            }
            if tools:
                payload["tools"] = tools
            result = await self._request_with_retries("/api/chat", payload)

        self._check_token_drift(messages, result)
        return result

    async def generate(self, prompt: str, **kwargs) -> dict[str, Any]:
        """Simple text generation."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": self._build_options(**kwargs),
        }
        return await self._request_with_retries("/api/generate", payload)

    def _check_token_drift(self, messages: list[dict[str, Any]], result: dict[str, Any]) -> None:
        """Compare Ollama's real prompt token count with our estimate."""
        if self.token_manager is None:
            return
        actual = result.get("prompt_eval_count")
        if not actual:
            return
        try:
            estimated = self.token_manager.count_messages_tokens(messages)
        except Exception:
            return
        if not estimated:
            return
        drift = (actual - estimated) / actual
        if abs(drift) > TOKEN_DRIFT_WARNING_THRESHOLD:
            logger.warning(
                "Token estimate drift detected",
                estimated=estimated,
                actual=actual,
                drift=f"{drift:+.0%}",
            )

    @staticmethod
    def extract_thinking_and_answer(content: str) -> tuple[str, str]:
        """Extract thinking and answer from model output.

        Three cases (ADR-011 + stage 6.2b):
        1. <think>...</think>      — block stripped, rest is the answer
        2. <think> without closing — everything after the opener is
           thinking, answer is empty
        3. </think> without opener — everything before the tag is junk,
           everything after is the answer
        """
        if not content:
            return "", ""

        match = re.search(r"<think>(.*?)</think>", content, re.DOTALL | re.IGNORECASE)
        if match:
            thinking = match.group(1).strip()
            answer = content[: match.start()] + content[match.end() :]
            return thinking, answer.strip()

        open_match = re.search(r"<think>", content, re.IGNORECASE)
        if open_match:
            thinking = content[open_match.end() :].strip()
            answer = content[: open_match.start()].strip()
            return thinking, answer

        close_match = re.search(r"</think>", content, re.IGNORECASE)
        if close_match:
            thinking = content[: close_match.start()].strip()
            answer = content[close_match.end() :].strip()
            return thinking, answer

        return "", content.strip()

    async def _request_with_retries(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Make request with exactly 2 retries. On failure: crash report."""
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                session = await self._get_session()
                url = f"{self.base_url}{endpoint}"
                logger.info(
                    "LLM request",
                    endpoint=endpoint,
                    model=self.model,
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                )
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(
                            "LLM response received",
                            endpoint=endpoint,
                            status=response.status,
                        )
                        return result

                    error_text = await response.text()
                    logger.error(
                        "LLM API error",
                        status=response.status,
                        error=error_text[:200],
                        attempt=attempt + 1,
                    )
                    last_error = f"HTTP {response.status}: {error_text[:200]}"
            except TimeoutError:
                logger.warning(
                    "LLM request timeout",
                    endpoint=endpoint,
                    timeout=self.timeout.total,
                    attempt=attempt + 1,
                )
                last_error = f"Timeout after {self.timeout.total}s"
            except aiohttp.ClientError as e:
                logger.warning(
                    "LLM connection error",
                    endpoint=endpoint,
                    error=str(e),
                    attempt=attempt + 1,
                )
                last_error = str(e)
            except Exception as e:
                logger.error(
                    "LLM unexpected error",
                    endpoint=endpoint,
                    error=str(e),
                    attempt=attempt + 1,
                )
                last_error = str(e)

            if attempt < self.max_retries:
                delay = self.retry_delay_base * (2**attempt)
                logger.info("Retrying after delay", delay_seconds=delay)
                await asyncio.sleep(delay)

        logger.critical(
            "LLM unavailable after all retries",
            endpoint=endpoint,
            attempts=self.max_retries + 1,
            last_error=last_error,
        )
        error = LLMUnavailableError(
            f"Ollama unavailable at {self.base_url} after "
            f"{self.max_retries + 1} attempts. Last error: {last_error}"
        )
        try:
            json_path, md_path = self._crash_reporter.generate_report(error)
            logger.error(
                "Crash report generated",
                json_path=str(json_path),
                md_path=str(md_path),
            )
        except Exception as report_error:
            logger.error("Failed to generate crash report", error=str(report_error))
        raise error
