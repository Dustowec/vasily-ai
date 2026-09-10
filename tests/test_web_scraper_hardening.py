"""Web scraper hardening tests: HTML size cap and DNS timeout (T3-017.6)."""

import asyncio
import socket
import time

import pytest
from aiohttp import web

from plugins.web_scraper import tool as scraper_module
from plugins.web_scraper.tool import WebScraperTool


@pytest.fixture
async def page_server():
    """Serves a valid HTML page with a large body."""

    async def handler(request):
        head = b"<html><head><title>BigPage</title></head><body>"
        filler = b"x" * 10000
        tail = b"</body></html>"
        return web.Response(body=head + filler + tail, content_type="text/html")

    app = web.Application()
    app.router.add_get("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    host, port = runner.addresses[0][:2]
    yield f"http://{host}:{port}/"
    await runner.cleanup()


@pytest.fixture
def allow_local(monkeypatch):
    """Permit local stub server despite SSRF protection."""
    monkeypatch.setattr(WebScraperTool, "_is_private_ip", staticmethod(lambda ip: False))


async def test_normal_page_scrapes_fully(page_server, allow_local):
    result = await WebScraperTool().execute(url=page_server)
    assert result["status"] == "success"
    assert result["source"] == "web"
    assert result["title"] == "BigPage"
    assert result["text_length"] >= 5000


async def test_oversized_html_is_truncated_not_fatal(page_server, allow_local, monkeypatch):
    monkeypatch.setattr(scraper_module, "MAX_HTML_BYTES", 200)
    result = await WebScraperTool().execute(url=page_server)
    assert result["status"] == "success"
    assert result["title"] == "BigPage"
    assert result["text_length"] < 5000


async def test_safe_resolver_fails_closed_on_dns_timeout(monkeypatch):
    """Slow DNS must be bounded and must fail closed (raise OSError), not pass."""
    monkeypatch.setattr(scraper_module, "DNS_TIMEOUT_SECONDS", 0.05)
    loop = asyncio.get_running_loop()

    async def slow_getaddrinfo(*args, **kwargs):
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(loop, "getaddrinfo", slow_getaddrinfo)

    resolver = scraper_module.SafeResolver()
    start = time.monotonic()
    with pytest.raises(OSError):
        await resolver.resolve("slow-dns.example")
    elapsed = time.monotonic() - start

    # Bounded: the timeout must fire well under the sleep duration.
    assert elapsed < 2.0


async def test_safe_resolver_fails_closed_on_dns_failure(monkeypatch):
    """Unresolvable hostname must fail closed (raise OSError), not pass."""
    loop = asyncio.get_running_loop()

    async def failing_getaddrinfo(*args, **kwargs):
        raise socket.gaierror("nope")

    monkeypatch.setattr(loop, "getaddrinfo", failing_getaddrinfo)

    resolver = scraper_module.SafeResolver()
    with pytest.raises(OSError):
        await resolver.resolve("does-not-exist.example")
