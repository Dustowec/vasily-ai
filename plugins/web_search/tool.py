"""Web Search plugin - searches via SearXNG."""

from typing import Any

import aiohttp

from core.base_tool import BaseTool
from core.logging_config import get_logger

logger = get_logger("plugins", "WebSearchTool")

SEARXNG_URL = "http://localhost:8080/search"


class WebSearchTool(BaseTool):
    """Search the web via SearXNG."""

    name = "web_search"
    description = "Search the web for information"
    version = "1.0.0"

    async def execute(self, query: str = "", limit: int = 5, **kwargs) -> dict[str, Any]:
        """Search web via SearXNG. Falls back to mock data if unavailable."""
        logger.info("Web search started", query=query, limit=limit)

        try:
            async with aiohttp.ClientSession() as session:
                params = {"q": query, "format": "json"}
                timeout = aiohttp.ClientTimeout(total=10)

                async with session.get(SEARXNG_URL, params=params, timeout=timeout) as response:
                    if response.status != 200:
                        logger.error("SearXNG error", status=response.status)
                        return self._mock_response(query, limit)

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
            logger.warning("SearXNG unavailable, using mock data", error=str(e))
            return self._mock_response(query, limit)

    def _mock_response(self, query: str, limit: int) -> dict[str, Any]:
        """Return mock data when SearXNG is unavailable."""
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
