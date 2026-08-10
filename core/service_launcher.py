"""Service launcher - auto-start for Ollama (TZ-024)."""

import asyncio
import subprocess
import sys

import aiohttp

from core.logging_config import get_logger

logger = get_logger("core", "ServiceLauncher")

POLL_INTERVAL_SECONDS = 0.5


async def _ollama_health(url: str) -> bool:
    """Quick health check: GET /api/tags with 2s timeout."""
    try:
        timeout = aiohttp.ClientTimeout(total=2.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{url}/api/tags") as response:
                return response.status == 200
    except Exception:
        return False


def _launch_ollama_process() -> None:
    """Launch 'ollama serve' as a detached background process."""
    if sys.platform == "win32":
        subprocess.Popen(
            ["ollama", "serve"],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    logger.info("Ollama process launched")


async def ensure_ollama_running(config) -> bool:
    """Ensure Ollama is running; auto-start if configured.

    Returns True if Ollama is reachable, False otherwise.
    """
    if await _ollama_health(config.llm_url):
        logger.info("Ollama already running", url=config.llm_url)
        return True

    if not config.llm_auto_start:
        logger.warning(
            "Ollama unavailable and auto-start disabled",
            url=config.llm_url,
        )
        return False

    logger.info("Attempting to auto-start Ollama", url=config.llm_url)
    _launch_ollama_process()

    deadline = asyncio.get_event_loop().time() + config.llm_auto_start_timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        if await _ollama_health(config.llm_url):
            logger.info("Ollama started successfully", url=config.llm_url)
            return True

    logger.error(
        "Ollama auto-start timed out",
        timeout=config.llm_auto_start_timeout,
    )
    return False
