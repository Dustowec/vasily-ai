"""AgentCore - orchestration layer with ReAct-powered routing."""

import asyncio
import signal
import time
from typing import Any

import structlog

from core.config import Config
from core.crash_reporter import install_async_exception_handler, install_crash_handler
from core.logging_config import get_logger, setup_logging
from core.plugin_registry import PluginRegistry
from core.react_loop import ReActLoop
from integrations.ollama_client import LLMUnavailableError, OllamaClient
from memory.manager import MemoryManager

logger = get_logger("core", "AgentCore")


class AgentCore:
    """Agent core with ReAct-powered request routing."""

    def __init__(self, config: Config):
        self.config = config
        self.plugin_registry = PluginRegistry()
        self.memory = MemoryManager()
        self.running = False

        self._start_time = time.time()
        self._requests_count = 0
        self._errors_count = 0

        self.llm_client: OllamaClient | None = None
        self.react_loop: ReActLoop | None = None
        self._active_request_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """Initialize all subsystems."""
        setup_logging(
            log_dir=self.config.log_dir,
            level=self.config.log_level,
            json_logs=self.config.json_logs,
        )
        install_crash_handler(self.config.log_dir)

        # Catch unhandled asyncio task exceptions into crash reports
        loop = asyncio.get_running_loop()
        install_async_exception_handler(loop, self.config.log_dir)

        logger.info(
            "Agent initializing",
            log_level=self.config.log_level,
            llm_url=self.config.llm_url,
        )

        self.plugin_registry.discover_plugins(self.config.plugins_dir)
        logger.info(
            "Plugins loaded",
            count=len(self.plugin_registry),
            plugins=self.plugin_registry.list_tools(),
        )

        self.llm_client = OllamaClient(
            base_url=self.config.llm_url,
            model=self.config.llm_model,
            timeout=self.config.llm_timeout,
            max_retries=self.config.llm_max_retries,
            num_ctx=self.config.llm_num_ctx,
            retry_delay_base=self.config.llm_retry_delay_base,
        )

        self.react_loop = ReActLoop(
            config=self.config,
            llm_client=self.llm_client,
            plugin_registry=self.plugin_registry,
        )

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

    def cancel_active_request(self) -> bool:
        """Cancel the active request task if any. Returns True if cancelled."""
        if self._active_request_task and not self._active_request_task.done():
            self._active_request_task.cancel()
            return True
        return False

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle user request using ReAct-powered routing."""
        self._requests_count += 1
        start = time.time()
        user_text = request.get("text", "")

        try:
            logger.info("Request received", text=user_text[:50])

            if user_text.strip().lower() == "status":
                return {"status": "success", "metrics": self.get_metrics()}

            if user_text.strip().lower() == "help":
                return {
                    "status": "success",
                    "message": "Available commands: status, help, exit. "
                    "Any other text is processed by AI with access to plugins. "
                    "Ctrl+C cancels the current request.",
                }

            if not self.react_loop:
                return {"status": "error", "message": "ReAct loop not initialized"}

            structlog.contextvars.bind_contextvars(request_id=f"req-{self._requests_count:04d}")

            result = await self.react_loop.run(user_text)

            duration_ms = (time.time() - start) * 1000
            logger.info(
                "Request completed",
                status=result.get("status"),
                iterations=result.get("iterations"),
                duration_ms=round(duration_ms, 2),
            )

            if result.get("status") == "success":
                return {
                    "status": "success",
                    "message": result.get("answer", ""),
                    "iterations": result.get("iterations"),
                    "steps": result.get("steps", []),
                }
            elif result.get("status") == "interrupted":
                return {
                    "status": "interrupted",
                    "message": result.get("answer", ""),
                    "iterations": result.get("iterations"),
                }
            else:
                return {
                    "status": "error",
                    "message": f"ReAct loop ended with status: {result.get('status')}",
                    "answer": result.get("answer", ""),
                }

        except LLMUnavailableError as e:
            self._errors_count += 1
            logger.error("LLM unavailable", error=str(e))
            return {
                "status": "error",
                "message": "AI is temporarily unavailable. Try again later.",
            }
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

                # Run request as a task so Ctrl+C can cancel it
                task = asyncio.create_task(self.handle_request({"id": "cli", "text": raw.strip()}))
                self._active_request_task = task
                try:
                    response = await task
                except asyncio.CancelledError:
                    response = {
                        "status": "interrupted",
                        "message": "Request cancelled by user.",
                    }
                finally:
                    self._active_request_task = None

                if response.get("status") == "success":
                    print(f"\n{response.get('message', '')}")
                    if "iterations" in response:
                        print(f"[Iterations: {response['iterations']}]")
                elif response.get("status") == "interrupted":
                    print(f"\n[Interrupted] {response.get('message', '')}")
                else:
                    print(f"\n[Error] {response.get('message', 'Unknown error')}")

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

        # Use LLM-powered compressor
        from memory.llm_compressor import LLMCompressor

        llm_compressor = LLMCompressor(self.llm_client)
        self.memory.start_background_compression(llm_compressor.compress)
        logger.info("LLM-powered memory compression enabled")

        try:
            await self._cli_loop()
        except asyncio.CancelledError:
            logger.info("Agent run loop cancelled")
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Graceful shutdown: save state, stop workers."""
        logger.info("Shutting down agent...")

        self.memory.stop_background_compression()

        if self.llm_client:
            await self.llm_client.close()

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


def setup_signal_handlers(agent: AgentCore) -> None:
    """First Ctrl+C cancels the active request; second shuts down."""

    def handle_sigint(signum, frame):
        if agent.cancel_active_request():
            logger.info("Ctrl+C: active request cancelled, partial progress returned")
        else:
            logger.info("Ctrl+C: shutting down")
            agent.running = False
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handle_sigint)


async def main():
    """Entry point."""
    config = Config.load()
    config.validate()

    agent = AgentCore(config)
    await agent.initialize()

    setup_signal_handlers(agent)

    try:
        await agent.run()
    except (asyncio.CancelledError, KeyboardInterrupt):
        await agent.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAgent interrupted by user")
