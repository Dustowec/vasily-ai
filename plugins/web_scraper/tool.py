"""Web Scraper plugin - extracts content from web pages.

SSRF-защита вынесена в пакет `security` (см. security/__init__.py).
Здесь остаётся только прикладная логика + тонкие шимы для обратной
совместимости со старыми тестами.
"""

from typing import Any
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from core.base_tool import BaseTool
from core.config import Config
from core.logging_config import get_logger
from core.plugin_types import make_error

# блок: SSRF-периметр — два публичных имени из security.
from security import MAX_URL_LENGTH, safe_connector
from security import preflight_check as _security_preflight
from security.ip_rules import forbidden_reason
from security.resolver import SafeResolver as _BaseSafeResolver

logger = get_logger("plugins", name="web_scraper")

# блок: константы, специфичные для скрейпинга.
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 5000
MAX_REDIRECTS = 5
REQUEST_TIMEOUT_SECONDS = 15
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}

# блок: legacy-константа для тестов (test_web_scraper_hardening monkeypatch'ит её).
# почему: DNS-таймаут теперь живёт в security.resolver, но старый тест
# ищет его именно здесь. Держим зеркало.
DNS_TIMEOUT_SECONDS = 3.0


class SafeResolver(_BaseSafeResolver):
    """Legacy-шим: при инстанцировании читает DNS_TIMEOUT_SECONDS
    из ЭТОГО модуля. Нужен, чтобы monkeypatch на scraper_module.DNS_TIMEOUT_SECONDS
    из test_web_scraper_hardening подхватывался.
    """

    def __init__(self, *args, **kwargs):
        # блок: подставляем локальный DNS_TIMEOUT_SECONDS как dns_timeout.
        # почему: глобальный lookup в момент создания экземпляра подхватывает
        # monkeypatch, сделанный на модуле.
        kwargs.setdefault("dns_timeout", DNS_TIMEOUT_SECONDS)
        super().__init__(*args, **kwargs)


class WebScraperTool(BaseTool):
    """Scrape content from web pages with SSRF protection."""

    name = "web_scraper"
    description = "Extract text content from a web page"
    version = "1.5.1"  # минор: совместимые шимы для старых тестов

    # ---- SSRF-шимы для совместимости со старыми тестами ----

    @staticmethod
    def _is_private_ip(ip):
        """Legacy-шим. Тесты monkeypatch'ат его, чтобы разрешить localhost.

        Возвращает True, если IP запрещён. Делегирует в security.ip_rules.
        """
        return forbidden_reason(ip) is not None

    def _preflight_check(self, url: str) -> str | None:
        """Legacy-шим для test_ssrf_edge_cases.

        Вызывает security.preflight_check с self._is_private_ip в роли
        кастомного чекера — чтобы monkeypatch на _is_private_ip работал.
        """
        return _security_preflight(url, is_ip_forbidden=self._is_private_ip)

    async def _validate_url(self, url: str) -> str | None:
        """Backward-compatible async wrapper для тестов."""
        return self._preflight_check(url)

    # ---- основной поток ----

    async def _execute(self, url: str = "", **kwargs) -> dict[str, Any]:
        """Scrape a web page with URL validation, SSRF protection and HTML size cap."""
        config = Config.load()

        # блок: валидация входа (наличие и тип url).
        if not url or not isinstance(url, str):
            return make_error(
                "invalid_url",
                "URL is required",
                "Provide a valid URL to scrape.",
            )

        # блок: защита от огромных URL.
        if len(url) > MAX_URL_LENGTH:
            return make_error(
                "invalid_url",
                f"URL too long ({len(url)} characters)",
                "Provide a shorter, valid URL to scrape.",
            )

        # блок: статический pre-flight через СВОЙ метод.
        # почему: метод проксирует security.preflight_check с
        # self._is_private_ip — тесты могут monkeypatch'ить IP-проверку.
        preflight = self._preflight_check(url)
        if preflight:
            logger.warning("SSRF preflight blocked", url=url, reason=preflight)
            return make_error(
                "invalid_url",
                f"URL is blocked: {preflight}",
                "Do not retry the same URL. Try an alternative external source.",
            )

        # блок: dev_mode — мок до сети.
        if config.dev_mode:
            logger.info("dev_mode returning mock", url=url)
            return self._mock_response(url)

        # блок: реальный fetch. Ошибки типизированы для LLM.
        try:
            return await self._fetch_with_redirects(url)
        except TimeoutError:
            logger.warning("SSRF request timeout", url=url)
            return make_error(
                "connection_failed",
                "Request timed out",
                "Target site unreachable or too slow. Do not retry the same URL.",
            )
        except aiohttp.ClientError as e:
            logger.warning("SSRF connection failed", url=url, error=str(e))
            return make_error(
                "connection_failed",
                f"Cannot reach target site: {e}",
                "Target site unreachable. Do not retry the same URL. "
                "Inform the user or try another source.",
            )
        except Exception as e:
            logger.exception("SSRF unexpected error", url=url)
            return make_error(
                "connection_failed",
                f"Unexpected error: {e}",
                "Do not retry the same URL. Try another source.",
            )

    # ---- сеть ----

    async def _fetch_with_redirects(self, url: str) -> dict[str, Any]:
        """Fetch URL с ручным следованием редиректам, валидируя каждый хоп."""
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        headers = {"User-Agent": "Mozilla/5.0 (Vasily AI Agent)"}

        # блок: safe_connector с кастомным IP-чекером.
        # почему: сохраняет поведение старого SafeResolver, который
        # делегировал проверку в self._is_private_ip.
        connector = safe_connector(is_ip_forbidden=self._is_private_ip)

        async with aiohttp.ClientSession(connector=connector) as session:
            current_url = url
            for hop in range(MAX_REDIRECTS + 1):
                async with session.get(
                    current_url,
                    timeout=timeout,
                    headers=headers,
                    allow_redirects=False,
                ) as response:
                    # блок: обработка редиректа.
                    if response.status in REDIRECT_STATUSES:
                        location = response.headers.get("Location")
                        if not location:
                            logger.warning(
                                "SSRF redirect without Location", url=current_url
                            )
                            return make_error(
                                "http_error",
                                "Redirect without Location header",
                                "Do not retry the same URL.",
                            )
                        new_url = urljoin(current_url, location)

                        # блок: preflight каждого хопа через СВОЙ метод.
                        # почему: тот же monkeypatch-хук, что и у входа.
                        hop_check = self._preflight_check(new_url)
                        if hop_check:
                            logger.warning(
                                "SSRF redirect blocked",
                                from_url=current_url,
                                to_url=new_url,
                                reason=hop_check,
                            )
                            return make_error(
                                "invalid_url",
                                f"Redirect blocked: {hop_check}",
                                "Do not retry. Redirect leads to a forbidden target.",
                            )
                        logger.info(
                            "SSRF redirect",
                            from_url=current_url,
                            to_url=new_url,
                            hop=hop + 1,
                        )
                        current_url = new_url
                        continue

                    # блок: любой не-200 и не-редирект = ошибка.
                    if response.status != 200:
                        return make_error(
                            "http_error",
                            f"Target site returned HTTP {response.status}",
                            "Do not retry the same URL. Try an alternative source "
                            "or inform the user that the page is inaccessible.",
                            http_status=response.status,
                        )

                    # блок: проверка Content-Type.
                    ct = (response.headers.get("Content-Type") or "").lower()
                    if ct and not self._is_textual(ct):
                        base = ct.split(";", 1)[0].strip()
                        logger.warning(
                            "SSRF unsupported content-type",
                            url=current_url,
                            content_type=base,
                        )
                        return make_error(
                            "unsupported_content_type",
                            f"Unsupported Content-Type: {base}",
                            "Target is not an HTML/text page. Do not retry.",
                        )

                    return await self._read_and_parse(response, current_url)

            # блок: лимит редиректов исчерпан.
            logger.warning("SSRF too many redirects", url=url)
            return make_error(
                "too_many_redirects",
                f"Too many redirects (>{MAX_REDIRECTS})",
                "Do not retry the same URL.",
            )

    async def _read_and_parse(self, response, url: str) -> dict[str, Any]:
        """Read bounded HTML, parse it, return text."""
        # блок: чтение с потолком. Читаем на 1 байт больше, чтобы узнать,
        # был ли файл обрезан.
        raw = await response.content.read(MAX_HTML_BYTES + 1)
        truncated = len(raw) > MAX_HTML_BYTES
        if truncated:
            raw = raw[:MAX_HTML_BYTES]

        # блок: декодирование с fallback на utf-8.
        encoding = response.charset or "utf-8"
        try:
            html = raw.decode(encoding, errors="replace")
        except LookupError:
            logger.warning("SSRF unknown charset", url=url, charset=encoding)
            html = raw.decode("utf-8", errors="replace")

        # блок: парсинг + вырезание скриптов/навигации.
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "nav", "footer"]):
            element.decompose()

        title = "No title"
        if soup.title and soup.title.string:
            title = soup.title.string.strip() or "No title"

        # блок: извлечение текста с обрезкой.
        text = soup.get_text(separator="\n", strip=True)
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "..."

        logger.info(
            "SSRF scraped",
            url=url,
            bytes=len(raw),
            truncated=truncated,
            chars=len(text),
        )
        return {
            "status": "success",
            "source": "web",
            "url": url,
            "title": title,
            "text_length": len(text),
            "truncated": truncated,
            "content": text,
        }

    @staticmethod
    def _is_textual(content_type: str) -> bool:
        base = content_type.split(";", 1)[0].strip()
        return base in ALLOWED_CONTENT_TYPES

    def _mock_response(self, url: str) -> dict[str, Any]:
        """Mock data for dev_mode only (used before any network call)."""
        return {
            "status": "success",
            "source": "mock",
            "url": url,
            "title": "Mock Page Title",
            "text_length": 100,
            "truncated": False,
            "content": f"Mock content scraped from {url}. This is simulated data.",
        }

    def _get_parameters(self) -> dict[str, Any]:
        return {
            "url": {"type": "string", "description": "URL to scrape", "required": True},
        }
