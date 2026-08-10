"""Danbooru plugin - searches tags and posts."""

from typing import Any

import aiohttp

from core.base_tool import BaseTool
from core.config import Config
from core.logging_config import get_logger
from core.plugin_types import make_error

logger = get_logger("plugins", "DanbooruTool")


class DanbooruTool(BaseTool):
    """Search Danbooru for tags and posts."""

    name = "danbooru_search"
    description = "Search Danbooru for anime art tags and posts"
    version = "1.1.0"

    async def execute(self, query: str = "", limit: int = 10, **kwargs) -> dict[str, Any]:
        """Search Danbooru posts by tags.

        dev_mode: mock data on backend failure.
        production: typed PluginErrorResult on backend failure.
        """
        query, limit = self._validate_inputs(query, limit)
        config = Config.load()
        logger.info("Danbooru search started", query=query, limit=limit)

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{config.danbooru_url}/posts.json"
                params = {"tags": query, "limit": limit}
                timeout = aiohttp.ClientTimeout(total=10)
                async with session.get(url, params=params, timeout=timeout) as response:
                    if response.status != 200:
                        logger.error(
                            "Danbooru API error",
                            status=response.status,
                            error_type="http_error",
                        )
                        if config.dev_mode:
                            return self._mock_response(query, limit)
                        return make_error(
                            "http_error",
                            f"Danbooru API returned HTTP {response.status}",
                            "Danbooru backend is malfunctioning. Do not retry the "
                            "same call. Inform the user or try another tool.",
                            http_status=response.status,
                        )
                    posts = await response.json()
                    logger.info("Danbooru search complete", results_count=len(posts))
                    return {
                        "status": "success",
                        "source": "api",
                        "query": query,
                        "results_count": len(posts),
                        "posts": [
                            {
                                "id": p.get("id"),
                                "tags": p.get("tag_string", ""),
                                "rating": p.get("rating", ""),
                                "score": p.get("score", 0),
                            }
                            for p in posts[:limit]
                        ],
                    }
        except Exception as e:
            logger.error("Danbooru API unavailable", error=str(e), error_type="connection_failed")
            if config.dev_mode:
                return self._mock_response(query, limit)
            return make_error(
                "connection_failed",
                f"Cannot connect to Danbooru API: {e}",
                "Danbooru backend unavailable. Do not retry the same call. "
                "Inform the user and suggest trying later.",
            )

    @staticmethod
    def _validate_inputs(query: Any, limit: Any) -> tuple[str, int]:
        """Clamp plugin inputs to safe ranges (T3-017.6)."""
        query = str(query)[:500]
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 10
        return query, min(max(limit, 1), 100)

    def _mock_response(self, query: str, limit: int) -> dict[str, Any]:
        """Return mock data when API is unavailable (dev_mode only)."""
        logger.info("Using mock data", query=query)
        return {
            "status": "success",
            "source": "mock",
            "query": query,
            "results_count": limit,
            "posts": [
                {
                    "id": i + 1,
                    "tags": f"{query}, highres, detailed",
                    "rating": "g",
                    "score": 100 - i * 10,
                }
                for i in range(limit)
            ],
        }

    def _get_parameters(self) -> dict[str, Any]:
        """Get parameter schema."""
        return {
            "query": {"type": "string", "description": "Tags to search", "required": True},
            "limit": {"type": "integer", "description": "Max results", "required": False},
        }
