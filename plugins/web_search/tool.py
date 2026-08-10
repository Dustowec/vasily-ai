"""Web Search plugin - searches via SearXNG."""

from typing import Any

import aiohttp

from core.base_tool import BaseTool
from core.config import Config
from core.logging_config import get_logger
from core.plugin_types import make_error

logger = get_logger("plugins", "WebSearchTool")


class WebSearchTool(BaseTool):
    """Search the web via SearXNG."""

    name = "web_search"
    description = "Search the web for information"
    version = "1.1.0"

    async def execute(self, query: str = "", limit: int = 5, **kwargs) -> dict[str, Any]:
        """Search web via SearXNG.

        dev_mode: mock data on backend failure.
        production: typed PluginErrorResult on backend failure.
        """
        query, limit = self._validate_inputs(query, limit)
        config = Config.load()
        logger.info("Web search started", query=query, limit=limit)

        try:
            async with aiohttp.ClientSession() as session:
                params = {"q": query, "format": "json"}
                timeout = aiohttp.ClientTimeout(total=10)
                async with session.get(
                    config.searxng_url, params=params, timeout=timeout
                ) as response:
                    if response.status != 200:
                        logger.error(
                            "SearXNG error",
                            status=response.status,
                            error_type="http_error",
                        )
                        if config.dev_mode:
                            return self._mock_response(query, limit)
                        return make_error(
                            "http_error",
                            f"Search backend returned HTTP {response.status}",
                            "Search backend is malfunctioning. Do not retry the "
                            "same call. Inform the user or try another tool.",
                            http_status=response.status,
                        )
                    data = await response.json()
                    results = data.get("results", [])[:limit]
                    logger.info("Web search complete", results_count=len(results))
                    return {
                        "status": "success",
                        "source": "searxng",
                        "query": query,
                        "results_count": len(results),
                        "results": [
                            {
                                "title": r.get("title", ""),
                                "url": r.get("url", ""),
                                "snippet": r.get("content", ""),
                            }
                            for r in results
                        ],
                    }
        except Exception as e:
            logger.error("SearXNG unavailable", error=str(e), error_type="connection_failed")
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
        """Clamp plugin inputs to safe ranges (T3-017.6)."""
        query = str(query)[:500]
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 5
        return query, min(max(limit, 1), 100)

    def _mock_response(self, query: str, limit: int) -> dict[str, Any]:
        """Return mock data when SearXNG is unavailable (dev_mode only)."""
        logger.info("Using mock data", query=query)
        return {
            "status": "success",
            "source": "mock",
            "query": query,
            "results_count": limit,
            "results": [
                {
                    "title": f"Result {i+1} for '{query}'",
                    "url": f"https://example.com/{i+1}",
                    "snippet": f"Mock snippet about {query}",
                }
                for i in range(limit)
            ],
        }

    def _get_parameters(self) -> dict[str, Any]:
        """Get parameter schema."""
        return {
            "query": {"type": "string", "description": "Search query", "required": True},
            "limit": {"type": "integer", "description": "Max results", "required": False},
        }
