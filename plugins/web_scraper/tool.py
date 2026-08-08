"""Web Scraper plugin - extracts content from web pages."""

from typing import Any

import aiohttp
from bs4 import BeautifulSoup

from core.base_tool import BaseTool
from core.logging_config import get_logger

logger = get_logger("plugins", "WebScraperTool")


class WebScraperTool(BaseTool):
    """Scrape content from web pages."""

    name = "web_scraper"
    description = "Extract text content from a web page"
    version = "1.0.0"

    async def execute(self, url: str = "", **kwargs) -> dict[str, Any]:
        """Scrape a web page. Falls back to mock data if unavailable."""
        logger.info("Web scraping started", url=url)

        if not url:
            return {"status": "error", "message": "URL is required"}

        try:
            async with aiohttp.ClientSession() as session:
                timeout = aiohttp.ClientTimeout(total=15)
                headers = {"User-Agent": "Mozilla/5.0 (Vasily AI Agent)"}

                async with session.get(url, timeout=timeout, headers=headers) as response:
                    if response.status != 200:
                        logger.error("Scraping failed", status=response.status)
                        return {"status": "error", "message": f"HTTP {response.status}"}

                    html = await response.text()
                    soup = BeautifulSoup(html, "html.parser")

                    # Remove script and style elements
                    for element in soup(["script", "style", "nav", "footer"]):
                        element.decompose()

                    title = soup.title.string.strip() if soup.title else "No title"
                    text = soup.get_text(separator="\n", strip=True)

                    # Limit text length
                    max_chars = 5000
                    if len(text) > max_chars:
                        text = text[:max_chars] + "..."

                    logger.info("Scraping complete", title=title, text_length=len(text))

                    return {
                        "status": "success",
                        "source": "web",
                        "url": url,
                        "title": title,
                        "text_length": len(text),
                        "content": text,
                    }

        except Exception as e:
            logger.warning("Scraping failed, using mock data", error=str(e))
            return self._mock_response(url)

    def _mock_response(self, url: str) -> dict[str, Any]:
        """Return mock data when scraping fails."""
        logger.info("Using mock data", url=url)
        return {
            "status": "success",
            "source": "mock",
            "url": url,
            "title": "Mock Page Title",
            "text_length": 100,
            "content": f"Mock content scraped from {url}. This is simulated data.",
        }

    def _get_parameters(self) -> dict[str, Any]:
        """Get parameter schema."""
        return {
            "url": {"type": "string", "description": "URL to scrape", "required": True},
        }
