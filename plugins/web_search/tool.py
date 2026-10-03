"""Web Search plugin - searches via SearXNG."""

from typing import Any
from urllib.parse import urlparse

import aiohttp

from core.base_tool import BaseTool
from core.config import Config
from core.logging_config import get_logger
from core.plugin_types import make_error

# блок: SSRF-защита для исходящих запросов к SearXNG.
from security import preflight_check, safe_connector

logger = get_logger("plugins", name="web_search")


def _backend_trusted_hosts(url: str) -> list[str]:
    """Возвращает [hostname] из URL бэкенда или [] при ошибке.

    Это «локальный» доверенный список для web_search: он НЕ попадает
    в глобальный allowlist и не влияет на другие инструменты.
    """
    # блок: парсим hostname из URL бэкенда.
    # почему: config.searxng_url может быть http://127.0.0.1:8888/ или
    # https://searx.be/ — в обоих случаях хотим именно хост без порта.
    try:
        h = urlparse(url).hostname
        return [h.lower()] if h else []
    except Exception:
        # блок: молча возвращаем пустой список.
        # почему: кривой URL в конфиге всё равно вызовет ошибку ниже
        # при попытке подключиться — не надо падать здесь.
        return []


class WebSearchTool(BaseTool):
    """Search the web via SearXNG."""

    name = "web_search"
    description = "Search the web for information"
    version = "1.4.0"  # минор: trusted_hosts для бэкенда

    async def _execute(
        self,
        query: str = "",
        limit: int = 5,
        language: str = "ru",
        **kwargs,
    ) -> dict[str, Any]:
        """Search web via SearXNG."""
        # блок: нормализация входа (обрезка, клампы).
        query, limit = self._validate_inputs(query, limit)
        config = Config.load()

        # блок: доверенный хост — бэкенд из конфига.
        # почему: config.searxng_url — осознанное решение пользователя.
        # Передаём его локально (не глобально) в preflight и connector.
        trusted = _backend_trusted_hosts(config.searxng_url)

        # блок: preflight бэкенд-URL с trusted_hosts.
        # почему: если бэкенд на loopback — это ок для web_search,
        # но НЕ делает loopback разрешённым для web_scraper.
        backend_check = preflight_check(config.searxng_url, trusted_hosts=trusted)
        if backend_check:
            logger.warning(
                "SSRF preflight blocked search backend",
                url=config.searxng_url,
                reason=backend_check,
            )
            return make_error(
                "invalid_url",
                f"Search backend URL is blocked: {backend_check}",
                "Add the backend host to Config.ssrf_allowed_hosts, "
                "or point searxng_url to a public instance.",
            )

        try:
            # блок: safe_connector с trusted_hosts для бэкенда.
            # почему: DNS-резолв SearXNG должен проходить через SafeResolver,
            # но loopback/private из конфига — пропускаться.
            async with aiohttp.ClientSession(
                connector=safe_connector(trusted_hosts=trusted)
            ) as session:
                params = {
                    "q": query,
                    "format": "json",
                    "language": language,
                }
                headers = {
                    "User-Agent": "Mozilla/5.0 (Vasily AI Agent; +local)",
                    "Accept": "application/json",
                }
                timeout = aiohttp.ClientTimeout(total=10)
                async with session.get(
                    config.searxng_url,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                ) as response:
                    # блок: не-200 — либо мок (dev), либо ошибка.
                    if response.status != 200:
                        if config.dev_mode:
                            return self._mock_response(query, limit)
                        return make_error(
                            "http_error",
                            f"Search backend returned HTTP {response.status}",
                            "Search backend is malfunctioning. Do not retry the "
                            "same call. Inform the user or try another tool.",
                            http_status=response.status,
                        )

                    # блок: проверка Content-Type.
                    content_type = (response.headers.get("Content-Type") or "").lower()
                    if "application/json" not in content_type:
                        if config.dev_mode:
                            return self._mock_response(query, limit)
                        return make_error(
                            "invalid_response",
                            f"Search backend returned non-JSON "
                            f"(Content-Type: {content_type})",
                            "Search backend is misconfigured (JSON format not "
                            "allowed). Do not retry. Try another instance or tool.",
                        )

                    # блок: парсинг JSON и обрезка до limit.
                    data = await response.json()
                    raw_results = data.get("results", [])[:limit]

                    # блок: дедупликация по URL.
                    seen: set[str] = set()
                    results = []
                    for r in raw_results:
                        url = r.get("url", "")
                        if url and url in seen:
                            continue
                        seen.add(url)
                        results.append(
                            {
                                "title": r.get("title", ""),
                                "url": url,
                                "snippet": r.get("content", ""),
                            }
                        )

                    return {
                        "status": "success",
                        "source": "searxng",
                        "query": query,
                        "results_count": len(results),
                        "results": results,
                    }
        except Exception as e:
            # блок: любая сетевая/парсинговая ошибка — мок или error.
            if config.dev_mode:
                return self._mock_response(query, limit)
            return make_error(
                "connection_failed",
                f"Cannot connect to search backend: {e}",
                "Search backend unavailable. Do not retry the same call. "
                "Inform the user and suggest trying later.",
            )

    @staticmethod
    def _validate_inputs(query: Any, limit: Any) -> tuple[str, int]:
        """Clamp plugin inputs to safe ranges."""
        query = str(query)[:500]
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 5
        return query, min(max(limit, 1), 100)

    def _mock_response(self, query: str, limit: int) -> dict[str, Any]:
        """Return mock data when SearXNG is unavailable (dev_mode only)."""
        return {
            "status": "success",
            "source": "mock",
            "query": query,
            "results_count": limit,
            "results": [
                {
                    "title": f"Result {i + 1} for '{query}'",
                    "url": f"https://example.com/{i + 1}",
                    "snippet": f"Mock snippet about {query}",
                }
                for i in range(limit)
            ],
        }

    def _get_parameters(self) -> dict[str, Any]:
        """Get parameter schema."""
        return {
            "query": {
                "type": "string",
                "description": "Search query",
                "required": True,
            },
            "limit": {
                "type": "integer",
                "description": "Max results",
                "required": False,
            },
            "language": {
                "type": "string",
                "description": "ISO 639-1 language code (ru, en). Default: ru",
                "required": False,
            },
        }
