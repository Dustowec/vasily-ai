"""AgentCore - minimal orchestration layer with interactive CLI."""

import asyncio
import signal
import time
from typing import Any

from core.config import Config
from core.crash_reporter import install_crash_handler
from core.logging_config import get_logger, setup_logging
from core.plugin_registry import PluginRegistry
from memory.manager import MemoryManager

logger = get_logger("core", "AgentCore")


class AgentCore:
    """Minimal agent core: config, plugins, memory, CLI loop."""

    def __init__(self, config: Config):
        self.config = config
        self.plugin_registry = PluginRegistry()
        self.memory = MemoryManager()
        self.running = False

        # Metrics
        self._start_time = time.time()
        self._requests_count = 0
        self._errors_count = 0

    async def initialize(self) -> None:
        """Initialize all subsystems."""
        setup_logging(
            log_dir=self.config.log_dir,
            level=self.config.log_level,
            json_logs=self.config.json_logs,
        )
        install_crash_handler(self.config.log_dir, self.config.crash_report_lines)

        logger.info(
            "Agent initializing",
            log_level=self.config.log_level,
            llm_url=self.config.llm_url,
        )

        # Discover plugins (path from config)
        self.plugin_registry.discover_plugins(self.config.plugins_dir)
        logger.info(
            "Plugins loaded",
            count=len(self.plugin_registry),
            plugins=self.plugin_registry.list_tools(),
        )

        # Health check
        await self.health_check()

        logger.info("Agent initialized successfully")

    async def health_check(self) -> dict[str, Any]:
        """Run full health check with colored report."""
        from core.health_check import HealthChecker

        checker = HealthChecker(
            config=self.config,
            plugin_registry=self.plugin_registry,
            memory_manager=self.memory,
        )
        report = await checker.run_all()
        checker.print_report(report)

        logger.info("Health check complete", overall=report.get("overall"))
        return report

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle user request with simple keyword routing."""
        self._requests_count += 1
        start = time.time()
        text = request.get("text", "")

        try:
            logger.info("Request received", text=text[:50])

            # Simple keyword routing (Phase 2 will replace with LLM router)
            if text.startswith("art "):
                plugin = self.plugin_registry.get("art_generator")
                if plugin:
                    result = await plugin.execute(subject=text[4:])
                    response = {"status": "success", "message": result.get("prompt", "")}
                else:
                    response = {"status": "error", "message": "Art plugin not found"}

            elif text.startswith("search "):
                plugin = self.plugin_registry.get("web_search")
                if plugin:
                    result = await plugin.execute(query=text[7:], limit=3)
                    response = {"status": "success", "results": result.get("results", [])}
                else:
                    response = {"status": "error", "message": "Search plugin not found"}

            elif text.startswith("scrape "):
                plugin = self.plugin_registry.get("web_scraper")
                if plugin:
                    result = await plugin.execute(url=text[7:])
                    response = {"status": "success", "content": result.get("content", "")[:500]}
                else:
                    response = {"status": "error", "message": "Scraper plugin not found"}

            elif text.startswith("tags "):
                plugin = self.plugin_registry.get("danbooru_search")
                if plugin:
                    result = await plugin.execute(query=text[5:], limit=3)
                    response = {"status": "success", "posts": result.get("posts", [])}
                else:
                    response = {"status": "error", "message": "Danbooru plugin not found"}

            elif text == "status":
                response = {"status": "success", "metrics": self.get_metrics()}

            elif text == "help":
                response = {
                    "status": "success",
                    "commands": [
                        "art <subject>",
                        "search <query>",
                        "scrape <url>",
                        "tags <tags>",
                        "status",
                        "help",
                        "exit",
                    ],
                }

            else:
                response = {"status": "success", "message": f"Unknown command: {text}"}

            duration_ms = (time.time() - start) * 1000
            logger.info("Request completed", duration_ms=round(duration_ms, 2))
            return response

        except Exception as e:
            self._errors_count += 1
            logger.error("Request failed", error=str(e))
            return {"status": "error", "message": str(e)}

    async def _cli_loop(self) -> None:
        """Async CLI loop - reads stdin without blocking asyncio."""
        loop = asyncio.get_running_loop()
        logger.info("CLI ready. Type 'help' for commands, 'exit' to quit.")

        while self.running:
            try:
                raw = await loop.run_in_executor(None, input, "\n> ")
                if not raw.strip():
                    continue
                if raw.strip().lower() == "exit":
                    self.running = False
                    break

                response = await self.handle_request({"id": "cli", "text": raw.strip()})

                # Pretty print response
                if response.get("status") == "success":
                    if "message" in response:
                        print(f"✓ {response['message']}")
                    elif "results" in response:
                        print(f"✓ Found {len(response['results'])} results:")
                        for r in response["results"]:
                            print(f"  - {r.get('title', r)}")
                    elif "posts" in response:
                        print(f"✓ Found {len(response['posts'])} posts:")
                        for p in response["posts"]:
                            print(f"  - ID {p.get('id')}: {p.get('tags', '')[:60]}")
                    elif "metrics" in response:
                        print(f"✓ Metrics: {response['metrics']}")
                    elif "commands" in response:
                        print("✓ Available commands:")
                        for cmd in response["commands"]:
                            print(f"  - {cmd}")
                    else:
                        print(f"✓ {response}")
                else:
                    print(f"✗ Error: {response.get('message', 'Unknown error')}")

            except EOFError:
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("CLI error", error=str(e))

    async def run(self) -> None:
        """Main agent loop with interactive CLI."""
        self.running = True
        logger.info("Agent started", plugins=len(self.plugin_registry))

        # Start background memory compression
        def simple_compressor(value: Any) -> str:
            text = str(value)
            return text[:100] + "..." if len(text) > 100 else text

        self.memory.start_background_compression(simple_compressor)

        try:
            await self._cli_loop()
        except asyncio.CancelledError:
            logger.info("Agent run loop cancelled")
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Graceful shutdown: save state, stop workers."""
        logger.info("Shutting down agent...")

        # Stop background tasks
        self.memory.stop_background_compression()

        # Log final metrics
        metrics = self.get_metrics()
        logger.info("Final metrics", **metrics)

        self.running = False
        logger.info("Agent shut down cleanly")

    def get_metrics(self) -> dict[str, Any]:
        """Get current agent metrics."""
        uptime = time.time() - self._start_time
        return {
            "uptime_seconds": round(uptime, 2),
            "requests_count": self._requests_count,
            "errors_count": self._errors_count,
            "plugins_loaded": len(self.plugin_registry),
            "memory_entries": len(self.memory),
        }


def setup_signal_handlers(agent: AgentCore, loop: asyncio.AbstractEventLoop) -> None:
    """Setup graceful shutdown on Ctrl+C / SIGTERM."""

    def handle_signal():
        logger.info("Shutdown signal received")
        agent.running = False
        for task in asyncio.all_tasks(loop):
            task.cancel()

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal)
    except NotImplementedError:
        # Windows doesn't fully support add_signal_handler
        pass


async def main():
    """Entry point."""
    config = Config.load()
    config.validate()

    agent = AgentCore(config)
    await agent.initialize()

    loop = asyncio.get_running_loop()
    setup_signal_handlers(agent, loop)

    try:
        await agent.run()
    except (asyncio.CancelledError, KeyboardInterrupt):
        await agent.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAgent interrupted by user")
