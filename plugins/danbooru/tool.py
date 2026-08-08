"""Danbooru plugin - searches tags and posts."""

from typing import Any

import aiohttp

from core.base_tool import BaseTool
from core.logging_config import get_logger

logger = get_logger("plugins", "DanbooruTool")

DANBOORU_API = "https://danbooru.donmai.us"


class DanbooruTool(BaseTool):
    """Search Danbooru for tags and posts."""

    name = "danbooru_search"
    description = "Search Danbooru for anime art tags and posts"
    version = "1.0.0"

    async def execute(self, query: str = "", limit: int = 10, **kwargs) -> dict[str, Any]:
        """Search Danbooru posts by tags. Falls back to mock data if API unavailable."""
        logger.info("Danbooru search started", query=query, limit=limit)

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{DANBOORU_API}/posts.json"
                params = {"tags": query, "limit": limit}
                timeout = aiohttp.ClientTimeout(total=10)

                async with session.get(url, params=params, timeout=timeout) as response:
                    if response.status != 200:
                        logger.error("Danbooru API error", status=response.status)
                        return self._mock_response(query, limit)

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
            logger.warning("Danbooru API unavailable, using mock data", error=str(e))
            return self._mock_response(query, limit)

    def _mock_response(self, query: str, limit: int) -> dict[str, Any]:
        """Return mock data when API is unavailable."""
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
