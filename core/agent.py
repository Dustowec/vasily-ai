"""AgentCore - orchestration layer with ReAct-powered routing."""

import asyncio
import signal
import time
from typing import Any

import structlog

from core.config import Config
from core.crash_reporter import install_async_exception_handler, install_crash_handler
from core.logging_config import get_logger, setup_logging
from core.metrics import MetricsCollector
from core.plugin_registry import PluginRegistry
from core.react_loop import ReActLoop
from core.scheduler import PeriodicScheduler
from core.service_launcher import ensure_ollama_running
from core.watchdog import Watchdog
from integrations.ollama_client import LLMUnavailableError, OllamaClient
from memory.manager import GradientMemory

logger = get_logger("core", "AgentCore")

# ADR-005: internal periodic task intervals (seconds)
COMPRESSION_INTERVAL_SECONDS = 6 * 3600
DIALOGUE_RESET_INTERVAL_SECONDS = 30 * 60


class AgentCore:
    """Agent core with ReAct-powered request routing."""

    def __init__(self, config: Config):
        self.config = config
        self.plugin_registry = PluginRegistry()
        self.memory = GradientMemory(data_dir=str(config.data_dir))
        self.metrics = MetricsCollector()
        self.running = False
        self._start_time = time.time()
        self._requests_count = 0
        self._errors_count = 0
        self.llm_client: OllamaClient | None = None
        self.react_loop: ReActLoop | None = None
        self._active_request_task: asyncio.Task | None = None
        self.scheduler: PeriodicScheduler | None = None
        self._session_requests = 0
        self.watchdog: Watchdog | None = None

    async def initialize(self) -> None:
        """Initialize all subsystems."""
        setup_logging(
            log_dir=self.config.log_dir,
            level=self.config.log_level,
            json_logs=self.config.json_logs,
        )
        install_crash_handler(self.config.log_dir)
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

        await ensure_ollama_running(self.config)
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
        self._session_requests = min(self._session_requests + 1, 1000)
        start = time.time()
        user_text = request.get("text", "")

        try:
            logger.info("Request received", text=user_text[:50])

            cmd = user_text.strip().lower()

            if cmd == "status":
                stats = self.memory.get_stats()
                icons = self.watchdog.get_status_icons() if self.watchdog else ""
                metrics = self.get_metrics()
                msg = (
                    f"Память: TGS={stats['tgs']}, Hot={stats['hot']}, Cold={stats['cold']}. "
                    f"Запросов: {metrics['requests_count']}, Ошибок: {metrics['errors_count']}. "
                    f"Watchdog: {icons}"
                )
                return {
                    "status": "success",
                    "message": msg,
                    "metrics": metrics,
                    "memory_stats": stats,
                    "watchdog_icons": icons,
                }

            if cmd == "help":
                return {
                    "status": "success",
                    "message": "Available commands: status, help, exit, забыть <тема>, забыть всё. "
                    "Any other text is processed by AI with access to plugins. "
                    "Ctrl+C cancels the current request.",
                }

            # ---- ОБРАБОТКА "ЗАБЫТЬ ВСЁ" ----
            if "забудь всё" in cmd or "забыть всё" in cmd:
                if "да" in cmd:
                    result = await self.memory.forget_all(confirm=True)
                    if result:
                        # Пересоздаём ReActLoop для полного сброса контекста
                        self.react_loop = ReActLoop(
                            config=self.config,
                            llm_client=self.llm_client,
                            plugin_registry=self.plugin_registry,
                        )
                        return {
                            "status": "success",
                            "message": "Память полностью очищена (ротация выполнена).",
                        }
                    return {"status": "error", "message": "Не удалось выполнить ротацию памяти."}
                else:
                    return {
                        "status": "error",
                        "message": "Для подтверждения команды 'забудь всё' требуется двойное подтверждение. "
                        "Введите 'забудь всё да' для подтверждения.",
                    }

            # ---- ЗАБЫТЬ КОНКРЕТНУЮ ТЕМУ ----
            if cmd.startswith("забудь") or cmd.startswith("забыть"):
                parts = cmd.split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    return {"status": "error", "message": "Укажите тему для забывания."}
                topic = parts[1].strip()
                result = await self.memory.forget(topic)
                if result:
                    return {"status": "success", "message": f"Тема '{topic}' забыта."}
                return {"status": "error", "message": f"Тема '{topic}' не найдена."}

            # ---- АВТОМАТИЧЕСКИЙ ПОИСК ----
            search_keywords = [
                "поищи",
                "найди",
                "погода",
                "новости",
                "цена",
                "курс",
                "сколько стоит",
                "узнай",
                "расскажи про",
                "что такое",
                "как работает",
                "когда",
                "где",
                "кто такой",
                "что происходит",
            ]
            text_lower = user_text.lower()
            if any(kw in text_lower for kw in search_keywords):
                web_search_tool = self.plugin_registry.get("web_search")
                if web_search_tool:
                    logger.info(
                        "Auto-detected search query, calling web_search directly",
                        text=user_text[:50],
                    )
                    try:
                        result = await web_search_tool.execute(query=user_text, limit=5)
                        if result.get("status") == "success":
                            results = result.get("results", [])
                            if results:
                                answer = f"Результаты поиска по запросу '{user_text}':\n\n"
                                for i, r in enumerate(results[:5], 1):
                                    title = r.get("title", "Без названия")
                                    snippet = r.get("snippet", "")
                                    url = r.get("url", "")
                                    answer += f"{i}. **{title}**\n"
                                    if snippet:
                                        answer += f"   {snippet}\n"
                                    if url:
                                        answer += f"   Источник: {url}\n"
                                    answer += "\n"
                                return {"status": "success", "message": answer, "iterations": 0}
                            else:
                                return {
                                    "status": "success",
                                    "message": f"По запросу '{user_text}' ничего не найдено.",
                                }
                        else:
                            error_msg = result.get("message", "Неизвестная ошибка при поиске")
                            return {"status": "error", "message": f"Ошибка поиска: {error_msg}"}
                    except Exception as e:
                        logger.error("Web search failed", error=str(e))
                        return {
                            "status": "error",
                            "message": f"Ошибка при выполнении поиска: {str(e)}",
                        }
                else:
                    logger.warning("web_search plugin not found, falling back to ReAct")

            # ---- ЕСЛИ НЕ КОМАНДА, ЗАПУСКАЕМ ReAct ----
            if not self.react_loop:
                return {"status": "error", "message": "ReAct loop not initialized"}

            structlog.contextvars.bind_contextvars(request_id=f"req-{self._requests_count:04d}")
            memory_context = await self.memory.build_context(user_text)

            result = await self.react_loop.run(
                user_text, memory_context=memory_context, prompt_type="default"
            )

            duration_ms = (time.time() - start) * 1000
            logger.info(
                "Request completed",
                status=result.get("status"),
                iterations=result.get("iterations"),
                duration_ms=round(duration_ms, 2),
            )

            await self._store_dialogue(user_text, result)
            await self.memory.decay(self._session_requests)

            status = result.get("status")
            self.metrics.record_request(
                duration_ms=duration_ms,
                status=status,
                iterations=result.get("iterations", 0),
            )

            if status in ("success", "interrupted"):
                self.metrics.record_react_result(result)

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

    async def _store_dialogue(self, user_text: str, result: dict[str, Any]) -> None:
        """Store dialogue turn in gradient memory."""
        status = result.get("status")
        answer = str(result.get("answer", "") or "").strip()
        if status not in ("success", "interrupted"):
            return
        if not answer:
            return
        timestamp = time.time()

        # Жёсткий ключ вместо генерации по времени
        dialogue_key = "dialogue:last"

        await self.memory.remember(
            dialogue_key,
            {
                "user": str(user_text),
                "assistant": answer,
                "timestamp": timestamp,
            },
            complex_query=len(user_text) > 100,
        )

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
                    if "memory_stats" in response:
                        stats = response["memory_stats"]
                        print(
                            f"[Memory: TGS={stats['tgs']}, Hot={stats['hot']}, Cold={stats['cold']}]"
                        )
                    if "watchdog_icons" in response:
                        print(f"[Watchdog: {response['watchdog_icons']}]")
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

        from memory.llm_compressor import LLMCompressor

        llm_compressor = LLMCompressor(self.llm_client)

        self.scheduler = PeriodicScheduler()
        self.scheduler.register(
            "memory_compression",
            COMPRESSION_INTERVAL_SECONDS,
            lambda: self.memory.compress_cycle(llm_compressor.compress),
        )
        # Регистрация задачи сброса диалога
        self.scheduler.register(
            "dialogue_reset",
            DIALOGUE_RESET_INTERVAL_SECONDS,
            lambda: self.memory.forget("dialogue:last"),
        )
        await self.scheduler.start()
        logger.info("LLM-powered memory compression enabled (internal scheduler)")

        if self.config.watchdog_enabled:
            self.watchdog = Watchdog(
                agent=self,
                check_interval=self.config.watchdog_check_interval,
                restart_timeout=self.config.watchdog_restart_timeout,
                max_restarts=self.config.watchdog_max_restarts,
            )
            await self.watchdog.start()
            logger.info(
                "Watchdog started",
                interval=self.config.watchdog_check_interval,
                max_restarts=self.config.watchdog_max_restarts,
            )
        else:
            logger.info("Watchdog disabled by config")

        try:
            await self._cli_loop()
        except asyncio.CancelledError:
            logger.info("Agent run loop cancelled")
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Graceful shutdown: save state, stop workers."""
        logger.info("Shutting down agent...")

        if self.watchdog:
            await self.watchdog.stop()
        if self.scheduler:
            await self.scheduler.stop()
        if self.llm_client:
            await self.llm_client.close()

        metrics = self.get_metrics()
        logger.info("Final metrics", **metrics)

        self.running = False
        logger.info("Agent shut down cleanly")

    def get_metrics(self) -> dict[str, Any]:
        """Get current agent metrics."""
        uptime = time.time() - self._start_time
        stats = self.memory.get_stats()

        base_metrics = {
            "uptime_seconds": round(uptime, 2),
            "requests_count": self._requests_count,
            "errors_count": self._errors_count,
            "plugins_loaded": len(self.plugin_registry),
            "memory_entries": stats["total"],
            "memory_tgs": stats["tgs"],
            "memory_hot": stats["hot"],
            "memory_cold": stats["cold"],
            "session_requests": self._session_requests,
        }

        if self.watchdog:
            watchdog_status = self.watchdog.get_status()
            base_metrics["watchdog_llm"] = "OK" if watchdog_status["llm"]["available"] else "FAIL"
            base_metrics["watchdog_plugins"] = (
                "OK" if watchdog_status["plugins"]["available"] else "FAIL"
            )
            base_metrics["watchdog_memory"] = (
                "OK" if watchdog_status["memory"]["available"] else "FAIL"
            )
            base_metrics["watchdog_disk"] = "OK" if watchdog_status["disk"]["available"] else "FAIL"

        base_metrics.update(self.metrics.snapshot())
        return base_metrics


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
