"""Plugin input validation tests (T3-017.6).

LLM-supplied arguments must be clamped to safe ranges before use.
"""

import pytest

from core.config import Config
from plugins.danbooru.tool import DanbooruTool
from plugins.web_scraper.tool import WebScraperTool
from plugins.web_search.tool import WebSearchTool

DEAD_URL = "http://this-host-does-not-exist-vasily-test.invalid"


@pytest.fixture
def dev_config(monkeypatch):
    """dev_mode with unreachable backends -> deterministic mock results."""
    monkeypatch.setattr(
        Config,
        "load",
        classmethod(
            lambda cls: Config(
                dev_mode=True,
                searxng_url=f"{DEAD_URL}/search",
                danbooru_url=DEAD_URL,
            )
        ),
    )


async def test_web_search_clamps_huge_limit(dev_config):
    result = await WebSearchTool().execute(query="q", limit=99999)
    assert result["status"] == "success"
    assert result["results_count"] == 100


async def test_web_search_clamps_negative_limit(dev_config):
    result = await WebSearchTool().execute(query="q", limit=-5)
    assert result["status"] == "success"
    assert result["results_count"] == 1


async def test_web_search_non_numeric_limit_falls_back(dev_config):
    result = await WebSearchTool().execute(query="q", limit="abc")
    assert result["status"] == "success"
    assert result["results_count"] == 5


async def test_web_search_truncates_long_query(dev_config):
    result = await WebSearchTool().execute(query="x" * 1000, limit=2)
    assert result["status"] == "success"
    assert len(result["query"]) == 500


async def test_danbooru_clamps_limit(dev_config):
    result = await DanbooruTool().execute(query="tag", limit=5000)
    assert result["status"] == "success"
    assert result["results_count"] == 100
    assert len(result["posts"]) == 100


async def test_web_scraper_rejects_too_long_url():
    long_url = "https://example.com/" + "a" * 5000
    result = await WebScraperTool().execute(url=long_url)
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_url"
