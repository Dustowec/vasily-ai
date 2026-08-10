"""Production error-handling test with a real 503 stub server (T3-017.5 Step 5)."""

import pytest
from aiohttp import web

from core.config import Config
from core.plugin_types import is_plugin_error
from core.react_loop import ReActLoop
from plugins.web_scraper.tool import WebScraperTool
from plugins.web_search.tool import WebSearchTool


@pytest.fixture
async def stub_server():
    """Local server that returns 503 for every request."""

    async def handler_503(request):
        return web.Response(status=503, text="Service Unavailable")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler_503)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    host, port = runner.addresses[0][:2]
    yield f"http://{host}:{port}"
    await runner.cleanup()


def patch_config(monkeypatch, stub_url):
    monkeypatch.setattr(
        Config,
        "load",
        classmethod(
            lambda cls: Config(
                dev_mode=False,
                searxng_url=f"{stub_url}/search",
                danbooru_url=stub_url,
            )
        ),
    )


class SpyWebSearch:
    """Counts real executions of the web_search plugin."""

    def __init__(self):
        self.calls = 0
        self.tool = WebSearchTool()

    async def execute(self, **kwargs):
        self.calls += 1
        return await self.tool.execute(**kwargs)


class StubRegistry:
    def __init__(self, spy):
        self._spy = spy

    def get_tools_schema(self):
        return [
            {
                "name": "web_search",
                "description": "Search the web for information",
                "parameters": {
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
                },
            }
        ]

    def get(self, name):
        return self._spy if name == "web_search" else None


class FakeLLM503:
    """Requests the same search twice, then explains unavailability."""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls <= 2:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "web_search",
                                "arguments": {"query": "x", "limit": 2},
                            }
                        }
                    ],
                }
            }
        return {
            "message": {
                "content": "The search service is temporarily unavailable. "
                "Please try again later."
            }
        }


async def test_web_search_returns_503_error(stub_server, monkeypatch):
    """Plugin must return a typed http_error with status 503."""
    patch_config(monkeypatch, stub_server)
    result = await WebSearchTool().execute(query="x", limit=2)
    assert is_plugin_error(result)
    assert result["error_type"] == "http_error"
    assert result["http_status"] == 503
    assert result["retry_advice"]


async def test_web_scraper_returns_503_error(stub_server, monkeypatch):
    """Scraper must return a typed http_error with status 503."""
    # Temporarily allow 127.0.0.1 for this test (stub server is local)
    monkeypatch.setattr(
        WebScraperTool,
        "_is_private_ip",
        staticmethod(lambda ip: False),
    )
    result = await WebScraperTool().execute(url=f"{stub_server}/page")
    assert is_plugin_error(result)
    assert result["error_type"] == "http_error"
    assert result["http_status"] == 503


async def test_react_handles_503_without_infinite_retry(stub_server, monkeypatch):
    """Full chain: 503 -> typed error -> dedup blocks repeat -> sane answer."""
    patch_config(monkeypatch, stub_server)
    spy = SpyWebSearch()
    loop = ReActLoop(
        config=Config.load(),
        llm_client=FakeLLM503(),
        plugin_registry=StubRegistry(spy),
    )
    result = await loop.run("search for x")

    # Real plugin executed once; second identical call blocked by dedup
    assert spy.calls == 1
    assert result["status"] == "success"
    assert result["iterations"] == 3
    assert result["iterations"] < Config.load().max_react_iterations

    previews = [step["result_preview"] for step in result["steps"]]
    assert "http_error" in previews[0]
    assert "DUPLICATE CALL" in previews[1]
    assert "unavailable" in result["answer"].lower()
