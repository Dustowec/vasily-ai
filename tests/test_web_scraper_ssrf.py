"""Tests for web_scraper SSRF protection (P3-2)."""

from plugins.web_scraper.tool import WebScraperTool


async def test_valid_external_url_passes():
    """External https URL should pass validation."""
    scraper = WebScraperTool()
    result = await scraper._validate_url("https://example.com/page")
    assert result is None  # None = valid


async def test_valid_http_url_passes():
    """External http URL should pass validation."""
    scraper = WebScraperTool()
    result = await scraper._validate_url("http://example.com")
    assert result is None


async def test_localhost_blocked():
    """localhost should be blocked."""
    scraper = WebScraperTool()
    result = await scraper._validate_url("http://localhost:8080/api")
    assert result is not None  # Any non-None string = blocked


async def test_localhost_ip_blocked():
    """127.0.0.1 should be blocked."""
    scraper = WebScraperTool()
    result = await scraper._validate_url("http://127.0.0.1:11434/api/tags")
    assert result is not None


async def test_private_ip_10_blocked():
    """10.x.x.x should be blocked."""
    scraper = WebScraperTool()
    result = await scraper._validate_url("http://10.0.0.1/internal")
    assert result is not None


async def test_private_ip_192_168_blocked():
    """192.168.x.x should be blocked."""
    scraper = WebScraperTool()
    result = await scraper._validate_url("http://192.168.1.1/admin")
    assert result is not None


async def test_link_local_blocked():
    """169.254.x.x should be blocked."""
    scraper = WebScraperTool()
    result = await scraper._validate_url("http://169.254.169.254/metadata")
    assert result is not None


async def test_file_scheme_blocked():
    """file:// scheme should be blocked."""
    scraper = WebScraperTool()
    result = await scraper._validate_url("file:///etc/passwd")
    assert result is not None


async def test_ftp_scheme_blocked():
    """ftp:// scheme should be blocked."""
    scraper = WebScraperTool()
    result = await scraper._validate_url("ftp://example.com/file")
    assert result is not None


async def test_empty_url_blocked():
    """Empty URL should be blocked."""
    scraper = WebScraperTool()
    result = await scraper._validate_url("")
    assert result is not None


async def test_scraper_returns_error_for_blocked_url():
    """Scraper should return structured error for blocked URL."""
    scraper = WebScraperTool()
    result = await scraper.execute(url="http://localhost:8080/admin")
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_url"
    assert "blocked" in result["message"].lower() or "internal" in result["message"].lower()
