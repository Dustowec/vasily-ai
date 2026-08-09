"""OllamaClient - async LLM client with retries and crash reporting.

Requirements (T3-015):
- Model: vasily-qwen (abliterated Qwen2.5-3B, q4_k_s, 32k context)
- Temperature: 0.1 (strict, for stable function calling)
- Async: aiohttp only (ADR-001)
- Logging: structlog with request_id (ADR-004)
- Resilience: 2 retries, then crash report and LLMUnavailableError
- Context window: num_ctx configurable, auto-injected into options
"""

import asyncio
from pathlib import Path
from typing import Any

import aiohttp

from core.crash_reporter import CrashReporter
from core.logging_config import get_logger

logger = get_logger("llm", "OllamaClient")

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "vasily-qwen"
DEFAULT_TEMPERATURE = 0.1
DEFAULT_TIMEOUT = 30.0
DEFAULT_NUM_CTX = 32768
MAX_RETRIES = 2
RETRY_DELAY_BASE = 1.0


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
        log_dir: str = "logs",
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.num_ctx = num_ctx
        self._session: aiohttp.ClientSession | None = None
        self._crash_reporter = CrashReporter(Path(log_dir))

    def _build_options(self, **kwargs) -> dict[str, Any]:
        """Build options dict. kwargs override defaults (including num_ctx)."""
        return {
            "temperature": self.temperature,
            "num_ctx": self.num_ctx,
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
        messages: list[dict[str, str]],
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

        return await self._request_with_retries("/api/chat", payload)

    async def generate(self, prompt: str, **kwargs) -> dict[str, Any]:
        """Simple text generation."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": self._build_options(**kwargs),
        }
        return await self._request_with_retries("/api/generate", payload)

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
                delay = RETRY_DELAY_BASE * (2**attempt)
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
