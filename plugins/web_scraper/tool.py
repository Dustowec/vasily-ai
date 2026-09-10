"""Web Scraper plugin - extracts content from web pages with SSRF protection."""

import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
from aiohttp.abc import AbstractResolver
from bs4 import BeautifulSoup

from core.base_tool import BaseTool
from core.config import Config
from core.logging_config import get_logger
from core.plugin_types import make_error

logger = get_logger("plugins", name="web_scraper")

ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = {80, 443, 8080, 8443}
DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_URL_LENGTH = 2048
MAX_HTML_BYTES = 2 * 1024 * 1024  # 2 MB cap on downloaded HTML
MAX_TEXT_CHARS = 5000
MAX_REDIRECTS = 5
DNS_TIMEOUT_SECONDS = 3.0
REQUEST_TIMEOUT_SECONDS = 15
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}

# CGNAT (RFC 6598) - not covered by ipaddress.is_private on Python 3.14 (verified).
CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def _forbidden_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Return a reason string if IP is forbidden, else None.

    Order matters: more specific categories are checked first, so that
    log messages carry the most actionable reason. E.g. 127.0.0.1 is both
    `loopback` and `private` on Python 3.14; we want `loopback`, because
    "loopback" tells an operator more than "private".
    """
    # IPv4-mapped IPv6: unwrap and check the IPv4 side explicitly.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        inner = _forbidden_reason(ip.ipv4_mapped)
        if inner:
            return f"ipv4_mapped:{inner}"

    # Most specific categories first.
    if ip.is_loopback:
        return "loopback"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_multicast:
        return "multicast"
    if ip.is_link_local:
        return "link_local"

    # CGNAT (RFC 6598): explicit check, is_private=False on Python 3.14.
    # Checked before `reserved` so it always wins for 100.64.0.0/10.
    if isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT_NETWORK:
        return "cgnat"

    # Broadest categories last.
    if ip.is_reserved:
        return "reserved"
    if ip.is_private:
        return "private"

    return None


class SafeResolver(AbstractResolver):
    """DNS resolver that blocks private/loopback/link-local IPs at connect time.

    Runs inside aiohttp's connector, so the resolved IP and the IP actually
    connected to are the same value. This closes the DNS rebinding (TOCTOU)
    hole that a pre-flight resolve-then-connect approach leaves open.

    Delegates the "is this IP forbidden" decision to
    WebScraperTool._is_private_ip so tests can monkeypatch it to allow
    local stub servers.
    """

    async def resolve(self, host, port=0, family=socket.AF_INET):
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM),
                timeout=DNS_TIMEOUT_SECONDS,
            )
        except TimeoutError as e:
            logger.warning("SSRF dns timeout", host=host, family=family)
            raise OSError(f"DNS timeout for {host}") from e
        except socket.gaierror as e:
            logger.warning("SSRF dns failure", host=host, family=family, error=str(e))
            raise OSError(f"DNS failure for {host}: {e}") from e

        result = []
        for fam, _type, proto, _canon, sockaddr in infos:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if WebScraperTool._is_private_ip(ip):
                reason = _forbidden_reason(ip) or "forbidden"
                logger.warning("SSRF blocked at resolve", host=host, ip=ip_str, reason=reason)
                raise OSError(f"Blocked IP {ip_str} ({reason})")
            result.append(
                {
                    "hostname": host,
                    "host": ip_str,
                    "port": port,
                    "family": fam,
                    "proto": proto,
                    "flags": 0,
                }
            )

        if not result:
            raise OSError(f"No usable addresses for {host}")
        return result

    async def close(self):
        return None


class WebScraperTool(BaseTool):
    """Scrape content from web pages with SSRF protection."""

    name = "web_scraper"
    description = "Extract text content from a web page"
    version = "1.4.1"

    async def _execute(self, url: str = "", **kwargs) -> dict[str, Any]:
        """Scrape a web page with URL validation, SSRF protection and HTML size cap."""
        config = Config.load()

        if not url or not isinstance(url, str):
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

        # Static pre-flight check: scheme, hostname, port, literal IP.
        # DNS resolution and per-IP validation happen later, at connect time,
        # inside SafeResolver - to avoid DNS rebinding (TOCTOU).
        preflight = self._preflight_check(url)
        if preflight:
            logger.warning("SSRF preflight blocked", url=url, reason=preflight)
            return make_error(
                "invalid_url",
                f"URL is blocked: {preflight}",
                "Do not retry the same URL. Try an alternative external source.",
            )

        # dev_mode returns a mock *before* hitting the network.
        # No mocks on real network failures - those must surface as errors.
        if config.dev_mode:
            logger.info("dev_mode returning mock", url=url)
            return self._mock_response(url)

        try:
            return await self._fetch_with_redirects(url)
        except TimeoutError:
            logger.warning("SSRF request timeout", url=url)
            return make_error(
                "connection_failed",
                "Request timed out",
                "Target site unreachable or too slow. Do not retry the same URL.",
            )
        except aiohttp.ClientError as e:
            logger.warning("SSRF connection failed", url=url, error=str(e))
            return make_error(
                "connection_failed",
                f"Cannot reach target site: {e}",
                "Target site unreachable. Do not retry the same URL. "
                "Inform the user or try another source.",
            )
        except Exception as e:
            logger.exception("SSRF unexpected error", url=url)
            return make_error(
                "connection_failed",
                f"Unexpected error: {e}",
                "Do not retry the same URL. Try another source.",
            )

    # ---- static validation ----

    def _preflight_check(self, url: str) -> str | None:
        """Static (no DNS) validation. Returns reason string or None if OK.

        Order matters:
          1. scheme
          2. hostname presence
          3. literal localhost names
          4. literal IP check (via self._is_private_ip - monkeypatchable)
          5. port whitelist, skipped for loopback IPs (test stub servers)
             or performed for hostnames (since we can't know their target IP yet)
        """
        try:
            parsed = urlparse(url)
        except Exception:
            return "parse_error"

        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            return f"blocked_scheme:{parsed.scheme}"

        hostname = parsed.hostname
        if not hostname:
            return "no_hostname"

        try:
            port = parsed.port
        except ValueError:
            return "invalid_port"
        if port is None:
            port = DEFAULT_PORTS[parsed.scheme.lower()]

        h = hostname.lower()
        if h in ("localhost", "localhost.localdomain"):
            return "localhost"

        # Try to parse hostname as a literal IP.
        lit = h.strip("[]")
        try:
            ip = ipaddress.ip_address(lit)
        except ValueError:
            # Hostname path: we don't know the target IP yet, so enforce
            # port whitelist unconditionally.
            if port not in ALLOWED_PORTS:
                return f"blocked_port:{port}"
            return None

        # Literal IP path: check IP first (monkeypatchable via _is_private_ip).
        if self._is_private_ip(ip):
            reason = _forbidden_reason(ip) or "forbidden"
            return f"literal_ip:{reason}:{ip}"

        # IP is allowed. Enforce port whitelist only for non-loopback IPs.
        # Rationale: in production, loopback is already blocked above; the
        # only way a loopback IP reaches this line is via a test monkeypatch,
        # and test stub servers bind to random high ports.
        if not ip.is_loopback and port not in ALLOWED_PORTS:
            return f"blocked_port:{port}"

        return None

    async def _validate_url(self, url: str) -> str | None:
        """Backward-compatible async wrapper around _preflight_check.

        Kept for existing tests. DNS-based validation now happens at connect
        time inside SafeResolver; this method performs the static check only.
        """
        return self._preflight_check(url)

    @staticmethod
    def _is_private_ip(
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> bool:
        """Return True if the IP should be blocked.

        Backward-compatible shim: delegates to the module-level
        _forbidden_reason helper. SafeResolver and _preflight_check call this
        via the class, so tests can monkeypatch it to allow local stub servers.
        """
        return _forbidden_reason(ip) is not None

    # ---- network ----

    async def _fetch_with_redirects(self, url: str) -> dict[str, Any]:
        """Fetch URL manually following redirects, validating each hop.

        The ClientSession owns the connector and closes it on exit; no
        explicit connector.close() is needed (and it's async in aiohttp 3.x,
        so calling it synchronously would leak a coroutine).
        """
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        headers = {"User-Agent": "Mozilla/5.0 (Vasily AI Agent)"}

        connector = aiohttp.TCPConnector(
            resolver=SafeResolver(),
            use_dns_cache=False,
            force_close=True,
        )

        async with aiohttp.ClientSession(connector=connector) as session:
            current_url = url
            for hop in range(MAX_REDIRECTS + 1):
                async with session.get(
                    current_url,
                    timeout=timeout,
                    headers=headers,
                    allow_redirects=False,
                ) as response:
                    if response.status in REDIRECT_STATUSES:
                        location = response.headers.get("Location")
                        if not location:
                            logger.warning("SSRF redirect without Location", url=current_url)
                            return make_error(
                                "http_error",
                                "Redirect without Location header",
                                "Do not retry the same URL.",
                            )
                        new_url = urljoin(current_url, location)
                        hop_check = self._preflight_check(new_url)
                        if hop_check:
                            logger.warning(
                                "SSRF redirect blocked",
                                from_url=current_url,
                                to_url=new_url,
                                reason=hop_check,
                            )
                            return make_error(
                                "invalid_url",
                                f"Redirect blocked: {hop_check}",
                                "Do not retry. Redirect leads to a forbidden target.",
                            )
                        logger.info(
                            "SSRF redirect",
                            from_url=current_url,
                            to_url=new_url,
                            hop=hop + 1,
                        )
                        current_url = new_url
                        continue

                    if response.status != 200:
                        return make_error(
                            "http_error",
                            f"Target site returned HTTP {response.status}",
                            "Do not retry the same URL. Try an alternative source "
                            "or inform the user that the page is inaccessible.",
                            http_status=response.status,
                        )

                    ct = (response.headers.get("Content-Type") or "").lower()
                    if ct and not self._is_textual(ct):
                        base = ct.split(";", 1)[0].strip()
                        logger.warning(
                            "SSRF unsupported content-type",
                            url=current_url,
                            content_type=base,
                        )
                        return make_error(
                            "unsupported_content_type",
                            f"Unsupported Content-Type: {base}",
                            "Target is not an HTML/text page. Do not retry.",
                        )

                    return await self._read_and_parse(response, current_url)

            logger.warning("SSRF too many redirects", url=url)
            return make_error(
                "too_many_redirects",
                f"Too many redirects (>{MAX_REDIRECTS})",
                "Do not retry the same URL.",
            )

    async def _read_and_parse(self, response, url: str) -> dict[str, Any]:
        """Read bounded HTML, parse it, return text."""
        raw = await response.content.read(MAX_HTML_BYTES + 1)
        truncated = len(raw) > MAX_HTML_BYTES
        if truncated:
            raw = raw[:MAX_HTML_BYTES]

        encoding = response.charset or "utf-8"
        try:
            html = raw.decode(encoding, errors="replace")
        except LookupError:
            logger.warning("SSRF unknown charset", url=url, charset=encoding)
            html = raw.decode("utf-8", errors="replace")

        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "nav", "footer"]):
            element.decompose()

        title = "No title"
        if soup.title and soup.title.string:
            title = soup.title.string.strip() or "No title"

        text = soup.get_text(separator="\n", strip=True)
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "..."

        logger.info(
            "SSRF scraped",
            url=url,
            bytes=len(raw),
            truncated=truncated,
            chars=len(text),
        )
        return {
            "status": "success",
            "source": "web",
            "url": url,
            "title": title,
            "text_length": len(text),
            "truncated": truncated,
            "content": text,
        }

    @staticmethod
    def _is_textual(content_type: str) -> bool:
        base = content_type.split(";", 1)[0].strip()
        return base in ALLOWED_CONTENT_TYPES

    def _mock_response(self, url: str) -> dict[str, Any]:
        """Mock data for dev_mode only (used before any network call)."""
        return {
            "status": "success",
            "source": "mock",
            "url": url,
            "title": "Mock Page Title",
            "text_length": 100,
            "truncated": False,
            "content": f"Mock content scraped from {url}. This is simulated data.",
        }

    def _get_parameters(self) -> dict[str, Any]:
        return {
            "url": {"type": "string", "description": "URL to scrape", "required": True},
        }
