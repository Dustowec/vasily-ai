"""Tests: plugins return structured errors on backend failure (T3-017.5 Step 1)."""

import pytest

from core.config import Config
from core.plugin_types import is_plugin_error
from plugins.danbooru.tool import DanbooruTool
from plugins.web_scraper.tool import WebScraperTool
from plugins.web_search.tool import WebSearchTool

DEAD_URL = "http://localhost:59999"


@pytest.fixture
def prod_config(monkeypatch):
    monkeypatch.setattr(
        Config,
        "load",
        classmethod(
            lambda cls: Config(
                dev_mode=False,
                searxng_url=f"{DEAD_URL}/search",
                danbooru_url=DEAD_URL,
            )
        ),
    )


@pytest.fixture
def dev_config(monkeypatch):
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


async def test_web_search_returns_structured_error(prod_config):
    result = await WebSearchTool().execute(query="test", limit=2)
    assert is_plugin_error(result)
    assert result["error_type"] in ("connection_failed", "http_error")
    assert result["retry_advice"]


async def test_danbooru_returns_structured_error(prod_config):
    result = await DanbooruTool().execute(query="test", limit=2)
    assert is_plugin_error(result)
    assert result["retry_advice"]


async def test_web_scraper_returns_structured_error(prod_config):
    result = await WebScraperTool().execute(url=f"{DEAD_URL}/page")
    assert is_plugin_error(result)
    assert result["error_type"] == "connection_failed"


async def test_web_scraper_invalid_url(prod_config):
    result = await WebScraperTool().execute(url="")
    assert is_plugin_error(result)
    assert result["error_type"] == "invalid_url"


async def test_web_search_dev_mode_returns_mock(dev_config):
    result = await WebSearchTool().execute(query="test", limit=2)
    assert result["status"] == "success"
    assert result["source"] == "mock"
