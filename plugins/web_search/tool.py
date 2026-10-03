"""Web Search plugin - searches via SearXNG."""

from typing import Any

import aiohttp

from core.base_tool import BaseTool
from core.config import Config
from core.logging_config import get_logger
from core.plugin_types import make_error

# блок: SSRF-защита для исходящих запросов к SearXNG.
# почему: если SearXNG когда-нибудь переедет на localhost — защита
# его не сломает, если хост добавлен в Config.ssrf_allowed_hosts.
from security import preflight_check, safe_connector

logger = get_logger("plugins", name="web_search")


class WebSearchTool(BaseTool):
    """Search the web via SearXNG."""

    name = "web_search"
    description = "Search the web for information"
    version = "1.3.0"  # минор: подключена SSRF-защита к бэкенду

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

        # блок: preflight бэкенд-URL.
        # почему: конфиг может быть изменён пользователем на локальный
        # хост, который не в allow-list — тогда лучше внятная ошибка,
        # чем тихий таймаут.
        backend_check = preflight_check(config.searxng_url)
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
            # блок: safe_connector вместо дефолтного.
            # почему: DNS-резолв SearXNG тоже должен проходить через SafeResolver.
            async with aiohttp.ClientSession(connector=safe_connector()) as session:
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
                    # почему: SearXNG может вернуть HTML, если format=json
                    # не разрешён в его настройках. Это конфиг-ошибка.
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
                    # почему: SearXNG агрегирует несколько движков,
                    # часто возвращает один URL несколько раз.
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
