"""Web Scraper plugin - extracts content from web pages with SSRF protection."""

import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

from core.base_tool import BaseTool
from core.config import Config
from core.plugin_types import make_error

ALLOWED_SCHEMES = ("http", "https")
MAX_URL_LENGTH = 2048
MAX_HTML_BYTES = 2 * 1024 * 1024  # 2 MB cap on downloaded HTML
DNS_TIMEOUT_SECONDS = 3.0


class WebScraperTool(BaseTool):
    """Scrape content from web pages with SSRF protection."""

    name = "web_scraper"
    description = "Extract text content from a web page"
    version = "1.2.0"

    async def _execute(self, url: str = "", **kwargs) -> dict[str, Any]:
        """Scrape a web page with URL validation and HTML size cap."""
        config = Config.load()

        if not url:
            return make_error(
                "invalid_url",
                "URL is required",
                "Provide a valid URL to scrape.",
            )

        if len(url) > MAX_URL_LENGTH:
            return make_error(
                "invalid_url",
                f"URL too long ({len(url)} characters)",
                "Provide a shorter, valid URL to scrape.",
            )

        # SSRF protection: validate URL before making request
        validation_error = await self._validate_url(url)
        if validation_error:
            return make_error(
                "invalid_url",
                f"URL is blocked: {validation_error}",
                "Do not retry the same URL. Try an alternative external source.",
            )

        try:
            async with aiohttp.ClientSession() as session:
                timeout = aiohttp.ClientTimeout(total=15)
                headers = {"User-Agent": "Mozilla/5.0 (Vasily AI Agent)"}
                async with session.get(url, timeout=timeout, headers=headers) as response:
                    if response.status != 200:
                        if config.dev_mode:
                            return self._mock_response(url)
                        return make_error(
                            "http_error",
                            f"Target site returned HTTP {response.status}",
                            "Do not retry the same URL. Try an alternative source "
                            "or inform the user that the page is inaccessible.",
                            http_status=response.status,
                        )

                    # Bounded read
                    raw = await response.content.read(MAX_HTML_BYTES + 1)
                    if len(raw) > MAX_HTML_BYTES:
                        raw = raw[:MAX_HTML_BYTES]
                    encoding = response.charset or "utf-8"
                    html = raw.decode(encoding, errors="replace")

                    soup = BeautifulSoup(html, "html.parser")
                    for element in soup(["script", "style", "nav", "footer"]):
                        element.decompose()
                    title = soup.title.string.strip() if soup.title else "No title"
                    text = soup.get_text(separator="\n", strip=True)
                    max_chars = 5000
                    if len(text) > max_chars:
                        text = text[:max_chars] + "..."
                    return {
                        "status": "success",
                        "source": "web",
                        "url": url,
                        "title": title,
                        "text_length": len(text),
                        "content": text,
                    }
        except Exception as e:
            if config.dev_mode:
                return self._mock_response(url)
            return make_error(
                "connection_failed",
                f"Cannot reach target site: {e}",
                "Target site unreachable. Do not retry the same URL. "
                "Inform the user or try another source.",
            )

    async def _validate_url(self, url: str) -> str | None:
        """Validate URL for SSRF protection."""
        if not url or not isinstance(url, str):
            return "empty_or_invalid"

        try:
            parsed = urlparse(url)
        except Exception:
            return "parse_error"

        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            return f"blocked_scheme:{parsed.scheme}"

        hostname = parsed.hostname
        if not hostname:
            return "no_hostname"

        if hostname.lower() in ("localhost", "localhost.localdomain"):
            return "localhost"

        try:
            ip = ipaddress.ip_address(hostname)
            if self._is_private_ip(ip):
                return f"private_ip:{ip}"
            return None
        except ValueError:
            pass

        try:
            infos = await self._resolve_hostname(hostname)
        except socket.gaierror:
            return None
        except Exception:
            return None

        if infos is None:
            return None

        for _family, _type, _proto, _canonname, sockaddr in infos:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
                if self._is_private_ip(ip):
                    return f"private_ip:{ip_str}"
            except ValueError:
                continue

        return None

    async def _resolve_hostname(self, hostname: str):
        """Resolve hostname with a bounded timeout."""
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.getaddrinfo(
                    hostname,
                    None,
                    family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                ),
                timeout=DNS_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return None

    @staticmethod
    def _is_private_ip(
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> bool:
        """Check if IP address is private, loopback, or link-local."""
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved

    def _mock_response(self, url: str) -> dict[str, Any]:
        """Return mock data when scraping fails (dev_mode only)."""
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
