"""Web Scraper plugin - extracts content from web pages with SSRF protection."""

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

from core.base_tool import BaseTool
from core.config import Config
from core.logging_config import get_logger
from core.plugin_types import make_error

logger = get_logger("plugins", "WebScraperTool")

ALLOWED_SCHEMES = ("http", "https")


class WebScraperTool(BaseTool):
    """Scrape content from web pages with SSRF protection."""

    name = "web_scraper"
    description = "Extract text content from a web page"
    version = "1.2.0"

    async def execute(self, url: str = "", **kwargs) -> dict[str, Any]:
        """Scrape a web page with URL validation.

        dev_mode: mock data on backend failure.
        production: typed PluginErrorResult on backend failure.
        """
        config = Config.load()
        logger.info("Web scraping started", url=url)

        if not url:
            return make_error(
                "invalid_url",
                "URL is required",
                "Provide a valid URL to scrape.",
            )

        # SSRF protection: validate URL before making request
        validation_error = await self._validate_url(url)
        if validation_error:
            logger.error(
                "URL validation failed",
                url=url,
                error_type=validation_error,
            )
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
                        logger.error(
                            "Scraping failed",
                            status=response.status,
                            error_type="http_error",
                        )
                        if config.dev_mode:
                            return self._mock_response(url)
                        return make_error(
                            "http_error",
                            f"Target site returned HTTP {response.status}",
                            "Do not retry the same URL. Try an alternative source "
                            "or inform the user that the page is inaccessible.",
                            http_status=response.status,
                        )
                    html = await response.text()
                    soup = BeautifulSoup(html, "html.parser")
                    for element in soup(["script", "style", "nav", "footer"]):
                        element.decompose()
                    title = soup.title.string.strip() if soup.title else "No title"
                    text = soup.get_text(separator="\n", strip=True)
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
            logger.error("Scraping failed", error=str(e), error_type="connection_failed")
            if config.dev_mode:
                return self._mock_response(url)
            return make_error(
                "connection_failed",
                f"Cannot reach target site: {e}",
                "Target site unreachable. Do not retry the same URL. "
                "Inform the user or try another source.",
            )

    async def _validate_url(self, url: str) -> str | None:
        """Validate URL for SSRF protection.

        Returns:
            None if URL is valid, or error type string if blocked.

        Checks:
            1. Scheme whitelist (http, https only)
            2. Hostname not empty
            3. Resolved IP addresses are not private/loopback/link-local
        """
        if not url or not isinstance(url, str):
            return "empty_or_invalid"

        try:
            parsed = urlparse(url)
        except Exception:
            return "parse_error"

        # Check scheme
        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            return f"blocked_scheme:{parsed.scheme}"

        hostname = parsed.hostname
        if not hostname:
            return "no_hostname"

        # Check for literal localhost
        if hostname.lower() in ("localhost", "localhost.localdomain"):
            return "localhost"

        # Try to parse as IP address directly
        try:
            ip = ipaddress.ip_address(hostname)
            if self._is_private_ip(ip):
                return f"private_ip:{ip}"
            return None  # Valid external IP
        except ValueError:
            # Not an IP address, treat as hostname
            pass

        # DNS resolution: check all resolved IPs
        try:
            # Use getaddrinfo to resolve hostname
            loop = None
            try:
                import asyncio

                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

            if loop:
                # Async DNS resolution
                infos = await loop.getaddrinfo(
                    hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
                )
            else:
                # Sync fallback (should not happen in async context)
                infos = socket.getaddrinfo(hostname, None)

            for _family, _type, _proto, _canonname, sockaddr in infos:
                ip_str = sockaddr[0]
                try:
                    ip = ipaddress.ip_address(ip_str)
                    if self._is_private_ip(ip):
                        return f"private_ip:{ip_str}"
                except ValueError:
                    continue

            return None  # All resolved IPs are external

        except socket.gaierror:
            # DNS resolution failed: not an SSRF issue, let aiohttp handle
            # the connection attempt and return its own error.
            return None
        except Exception as e:
            logger.warning("DNS resolution error", error=str(e))
            return None

    @staticmethod
    def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        """Check if IP address is private, loopback, or link-local."""
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved

    def _mock_response(self, url: str) -> dict[str, Any]:
        """Return mock data when scraping fails (dev_mode only)."""
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
