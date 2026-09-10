"""SSRF edge cases: literal IPs, CGNAT, multicast, v4-mapped, ports, redirects."""

import pytest

from plugins.web_scraper.tool import WebScraperTool


@pytest.mark.parametrize(
    "url, expected_reason_part",
    [
        # Loopback / private / link-local
        ("http://127.0.0.1/", "literal_ip:loopback"),
        ("http://10.0.0.1/", "literal_ip:private"),
        ("http://192.168.1.1/", "literal_ip:private"),
        ("http://169.254.169.254/", "literal_ip:link_local"),
        # v4-mapped IPv6
        ("http://[::ffff:127.0.0.1]/", "ipv4_mapped"),
        ("http://[::ffff:10.0.0.1]/", "ipv4_mapped"),
        # CGNAT (100.64.0.0/10) - explicit check
        ("http://100.64.0.1/", "literal_ip:cgnat"),
        ("http://100.127.255.255/", "literal_ip:cgnat"),
        # Multicast IPv6
        ("http://[ff02::1]/", "literal_ip:multicast"),
        # Unspecified
        ("http://0.0.0.0/", "literal_ip:unspecified"),
        # Localhost by name
        ("http://localhost:8080/", "localhost"),
        ("http://localhost.localdomain/", "localhost"),
        # Scheme
        ("file:///etc/passwd", "blocked_scheme:file"),
        ("ftp://example.com/file", "blocked_scheme:ftp"),
        ("gopher://example.com/", "blocked_scheme:gopher"),
        # Ports
        ("http://example.com:6379/", "blocked_port:6379"),
        ("http://example.com:22/", "blocked_port:22"),
        ("http://example.com:9200/", "blocked_port:9200"),
    ],
)
def test_preflight_blocks_forbidden(url, expected_reason_part):
    """Each forbidden URL must produce a reason containing the expected part."""
    reason = WebScraperTool()._preflight_check(url)
    assert reason is not None, f"Expected block for {url}, got None"
    assert (
        expected_reason_part in reason
    ), f"For {url}: expected '{expected_reason_part}' in reason, got '{reason}'"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "http://example.com/",
        "https://example.com:8080/",
        "https://example.com:8443/",
        "http://[2001:4860:4860::8888]/",  # public IPv6 (Google DNS)
    ],
)
def test_preflight_allows_public(url):
    """Public URLs must pass preflight."""
    assert WebScraperTool()._preflight_check(url) is None
