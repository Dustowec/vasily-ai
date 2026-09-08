"""OllamaClient - async LLM client with retries and crash reporting.
Stage 5a: num_predict passthrough, token drift calibration, honest
timeout handling, unclosed-think handling.
Stage 6.2b: third thinking case — CLOSING </think> without an opener
(tail-of-thinking without head): everything BEFORE the tag is thinking
junk, everything after is the answer. Qwen3.5 sometimes emits this.
"""

import asyncio
import re
from pathlib import Path
from typing import Any

import aiohttp

from core.crash_reporter import CrashReporter
from core.logging_config import get_logger

logger = get_logger("llm", "OllamaClient")

# Defaults kept in sync with core/config.py defaults (ADR-011).
DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "vasily-qwen"
DEFAULT_TEMPERATURE = 0.1
DEFAULT_TIMEOUT = 120.0
DEFAULT_NUM_CTX = 8192
DEFAULT_NUM_PREDICT = 3072
DEFAULT_RETRY_DELAY_BASE = 1.0
MAX_RETRIES = 2

TOKEN_DRIFT_WARNING_THRESHOLD = 0.15


class LLMUnavailableError(Exception):
    """Raised when LLM is unavailable after all retries."""

    pass


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
        """Send chat request to Ollama with optional function calling tools."""
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
        """Compare Ollama's real prompt token count with our estimate.
        Calibration for T3-018. Note: with tools attached, Ollama's
        prompt includes tool schemas our estimate does not count —
        a systematic positive drift there is EXPECTED."""
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
        Returns (thinking_text, answer_text).

        Three cases (ADR-011 + stage 6.2b):
        1. <think>...</think>      — block stripped, rest is the answer
        2. <think> without closing — generation cut mid-thought:
           everything after the opener is thinking, answer is empty
        3. </think> without opener — Qwen3.5 emits a TAIL of thinking
           without the head: everything BEFORE the tag is junk,
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
                # Python 3.10: asyncio.TimeoutError and builtin TimeoutError
                # are DIFFERENT classes; 3.11+ they are the same. Catch both.
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
