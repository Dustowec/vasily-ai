"""AgentCore - orchestration layer with ReAct-powered routing.
ADR-011: Sliding Window (5 pairs FIFO) instead of dialogue:last.
UTF-8 Hardening: Fixed double-encoding in dialogue compression.
Stage 5b: num_predict passthrough, deep-copied dialogue history,
search results stored to dialogue.
Stage 8: PeriodicScheduler removed. Compression is now lazy: after
every request a cheap scan checks for cooled HOT entries, and if any
exist, compression runs as a BACKGROUND task (never blocks the reply).
ADR-013 (6.3): cold_start_penalty() at startup (replaces the old
session_close decay); forget_all answers with counts (rotated /
amnestied / next free tick); session_close call removed.
ADR-014: Keyword routing removed for all tools. LLM decides via tool-calling.
Empty response safeguard for small models. Interactive forget with LLM classification.
"""

import asyncio
import json
import re
import signal
import time
import uuid
from typing import Any

import structlog

from core.config import Config
from core.crash_reporter import install_async_exception_handler, install_crash_handler
from core.internal_tools import ListFilesTool, RecallMemoryTool, RememberFactTool
from core.logging_config import get_logger, setup_logging
from core.metrics import MetricsCollector
from core.plugin_registry import PluginRegistry
from core.react_loop import ReActLoop
from core.service_launcher import ensure_ollama_running
from core.watchdog import Watchdog
from integrations.ollama_client import LLMUnavailableError, OllamaClient
from memory.manager import GradientMemory

logger = get_logger("core", "AgentCore")


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
        self._session_requests = 0
        self.watchdog: Watchdog | None = None
        self._dialogue_window: list[dict] = []
        self._dialogue_buffer: list[dict] = []
        self._llm_compressor = None
        self._compression_task: asyncio.Task | None = None
        self._forget_candidates: list[dict] = []
        self._forget_topic: str = ""

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

        # ADR-013 §3: стартовое охлаждение (−2.0 всей памяти, кроме TGS).
        await self.memory.cold_start_penalty()

        self.plugin_registry.discover_plugins(self.config.plugins_dir)

        self.llm_client = OllamaClient(
            base_url=self.config.llm_url,
            model=self.config.llm_model,
            timeout=self.config.llm_timeout,
            max_retries=self.config.llm_max_retries,
            num_ctx=self.config.llm_num_ctx,
            num_predict=self.config.llm_num_predict,
            retry_delay_base=self.config.llm_retry_delay_base,
        )

        recall_tool = RecallMemoryTool(self.memory, self.llm_client)
        self.plugin_registry.register(recall_tool)

        remember_tool = RememberFactTool(self.memory, self.llm_client)
        self.plugin_registry.register(remember_tool)

        list_files_tool = ListFilesTool()
        self.plugin_registry.register(list_files_tool)

        logger.info(
            "Plugins loaded",
            count=len(self.plugin_registry),
            plugins=self.plugin_registry.list_tools(),
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
                    "message": "Available commands: status, help, exit, забыть [текст], забыть всё, удалить [номера/всех]. "
                    "Any other text is processed by AI with access to plugins. "
                    "Ctrl+C cancels the current request.",
                }

            if "забудь всё" in cmd or "забыть всё" in cmd:
                if "да" in cmd:
                    result = await self.memory.forget_all(confirm=True)
                    if result.get("confirmed"):
                        self._dialogue_window.clear()
                        self._dialogue_buffer.clear()
                        self.react_loop = ReActLoop(
                            config=self.config,
                            llm_client=self.llm_client,
                            plugin_registry=self.plugin_registry,
                        )
                        msg = (
                            f"Память очищена: удалено {result['rotated']} записей, "
                            f"сохранено {result['amnestied']} свежих фактов. "
                            f"Повторить можно после тика {result['next_free_tick']}."
                        )
                        return {"status": "success", "message": msg}
                    return {"status": "error", "message": "Не удалось выполнить ротацию памяти."}
                else:
                    return {
                        "status": "error",
                        "message": "Для подтверждения команды 'забудь всё' требуется двойное подтверждение. "
                        "Введите 'забудь всё да' для подтверждения.",
                    }

            if cmd.startswith("забудь") or cmd.startswith("забыть"):
                parts = user_text.split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    return {"status": "error", "message": "Укажите тему для забывания."}
                topic = parts[1].strip()
                self._forget_topic = topic

                # 1. Поиск в базе памяти
                search_result = await self.memory.recall_memory(topic)
                if not search_result.get("found"):
                    return {"status": "success", "message": f"По теме '{topic}' ничего не найдено."}

                # 2. LLM классификация
                candidates = await self._llm_filter_forget(topic, search_result.get("facts", []))
                if not candidates:
                    return {"status": "success", "message": f"По теме '{topic}' ничего не найдено."}

                # 3. Сценарий А: только 1 user_fact -> молчаливое удаление
                if len(candidates) == 1 and candidates[0]["key"].startswith("user_fact:"):
                    await self.memory.forget(candidates[0]["key"])
                    await self.memory.redistill_summaries(topic, self._llm_rewrite_summary)
                    return {"status": "success", "message": f"Факт по теме '{topic}' удален."}

                # 4. Сценарий Б: несколько совпадений -> запрос юзеру
                self._forget_candidates = candidates
                msg = "Я нашел несколько упоминаний. Кого убиваем?\n"
                for i, c in enumerate(candidates):
                    text = c.get("summary") or str(c.get("value"))[:50]
                    msg += f"{i+1}. {text}\n"
                msg += "Напишите 'удалить 1, 2' или 'удалить всех'."
                return {"status": "success", "message": msg}

            if cmd.startswith("удалить"):
                if not self._forget_candidates:
                    return {"status": "error", "message": "Нет активных кандидатов на удаление."}

                parts = cmd.split(maxsplit=1)
                if len(parts) < 2:
                    return {"status": "error", "message": "Укажите номера или 'всех'."}

                choice = parts[1].strip()
                keys_to_delete = []

                if choice == "всех":
                    keys_to_delete = [c["key"] for c in self._forget_candidates]
                else:
                    try:
                        indices = [int(x.strip()) for x in choice.split(",")]
                        for idx in indices:
                            if 0 < idx <= len(self._forget_candidates):
                                keys_to_delete.append(self._forget_candidates[idx - 1]["key"])
                    except ValueError:
                        return {"status": "error", "message": "Неверный формат. Пример: 1, 2"}

                if not keys_to_delete:
                    return {"status": "error", "message": "Ничего не выбрано."}

                topic = self._forget_topic
                for key in keys_to_delete:
                    await self.memory.forget(key)

                if topic:
                    await self.memory.redistill_summaries(topic, self._llm_rewrite_summary)

                self._forget_candidates = []
                self._forget_topic = ""
                return {"status": "success", "message": f"Удалено записей: {len(keys_to_delete)}."}

            # === ADR-014: Keyword-роутинг полностью вырезан ===
            # LLM сама решает, вызывать инструменты или нет.

            if not self.react_loop:
                return {"status": "error", "message": "ReAct loop not initialized"}

            structlog.contextvars.bind_contextvars(request_id=f"req-{self._requests_count:04d}")

            dialogue_history = [dict(m) for m in self._dialogue_window]
            result = await self.react_loop.run(
                user_text, dialogue_history=dialogue_history, prompt_type="default"
            )

            # Защита от пустого ответа LLM
            if result.get("status") == "success" and not str(result.get("answer", "")).strip():
                result["answer"] = "Я задумался, но забыл ответить. Можешь переформулировать?"

            duration_ms = (time.time() - start) * 1000
            logger.info(
                "Request completed",
                status=result.get("status"),
                iterations=result.get("iterations"),
                duration_ms=round(duration_ms, 2),
            )

            await self._store_dialogue(user_text, result)

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
            elif result.get("status") == "failed":
                return {
                    "status": "failed",
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
            return {"status": "error", "message": "AI is temporarily unavailable. Try again later."}
        except Exception as e:
            self._errors_count += 1
            logger.error("Request failed", error=str(e))
            return {"status": "error", "message": str(e)}
        finally:
            # ADR-014: Инвариант «1 запрос = 1 тик» соблюдается при любом исходе
            await self.memory.decay(self._session_requests)
            self._maybe_compress_memory()

    async def _llm_filter_forget(self, topic: str, facts: list[dict]) -> list[dict]:
        """Классификация найденных фактов через LLM."""
        if not facts:
            return []

        prompt = f"Пользователь хочет забыть тему: '{topic}'. Вот найденные факты:\n"
        for i, f in enumerate(facts):
            text = f.get("summary") or str(f.get("value"))
            prompt += f"{i+1}. {text}\n"
        prompt += 'Верни ТОЛЬКО JSON-объект: {"ids": [1, 2]}. Где ids — номера фактов, которые СТРОГО относятся к теме.'

        try:
            resp = await self.llm_client.generate(prompt)
            match = re.search(r"\{.*\}", resp, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                ids = data.get("ids", [])
                return [facts[i - 1] for i in ids if 0 < i <= len(facts)]
        except Exception as e:
            logger.error("LLM filter for forget failed", error=str(e))
        return []

    async def _llm_rewrite_summary(self, text: str, topic: str) -> str:
        """Переписывает саммари, удаляя упоминания темы."""
        prompt = (
            f"Перепиши следующий текст, полностью удалив любую информацию о '{topic}'. "
            f"Если без этой информации текст теряет смысл, просто верни пустую строку. "
            f"Текст:\n{text}"
        )
        try:
            resp = await self.llm_client.generate(prompt)
            return resp.strip()
        except Exception as e:
            logger.error("LLM rewrite summary failed", error=str(e))
            return text

    def _maybe_compress_memory(self) -> None:
        """Kick off background HOT→COLD compression when cooled entries exist."""
        if self._compression_task and not self._compression_task.done():
            return
        try:
            if not self.memory.has_compression_candidates():
                return
        except Exception as e:
            logger.error("Compression candidate check failed", error=str(e))
            return
        self._compression_task = asyncio.create_task(self._run_compression())
        logger.info("Background memory compression started")

    async def _run_compression(self) -> None:
        """Background HOT→COLD compression worker (LLM calls live here)."""
        try:
            from memory.llm_compressor import LLMCompressor

            if self._llm_compressor is None:
                self._llm_compressor = LLMCompressor(self.llm_client)
            compressed = await self.memory.compress_cycle(self._llm_compressor.compress)
            if compressed:
                logger.info("Background memory compression done", compressed=compressed)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Background memory compression failed", error=str(e))

    async def _store_dialogue(self, user_text: str, result: dict[str, Any]) -> None:
        """Store dialogue turn in sliding window AND buffer for compression (ADR-011 fix)."""
        status = result.get("status")
        answer = str(result.get("answer", "") or result.get("message", "")).strip()

        if status not in ("success", "interrupted", "failed"):
            return
        if not answer:
            return

        self._dialogue_window.append({"role": "user", "content": user_text})
        self._dialogue_window.append({"role": "assistant", "content": answer})

        while len(self._dialogue_window) > 10:
            self._dialogue_window.pop(0)

        self._dialogue_buffer.append({"role": "user", "content": user_text})
        self._dialogue_buffer.append({"role": "assistant", "content": answer})

        if len(self._dialogue_buffer) >= 10:
            await self._compress_and_store_dialogue()

    async def _compress_and_store_dialogue(self, force: bool = False) -> None:
        """Compress dialogue buffer into a single fact and store in memory."""
        if not self._dialogue_buffer:
            return

        if not force and len(self._dialogue_buffer) < 10:
            return

        chunk_size = 10 if not force else len(self._dialogue_buffer)
        messages = self._dialogue_buffer[:chunk_size]
        self._dialogue_buffer = self._dialogue_buffer[chunk_size:]

        text_parts = []
        for msg in messages:
            role = "Пользователь" if msg["role"] == "user" else "Ассистент"
            content = msg.get("content", "")

            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            elif isinstance(content, str):
                if any(ord(c) > 127 for c in content):
                    try:
                        content = content.encode("latin1").decode("utf-8")
                    except (UnicodeError, LookupError):
                        pass

            text_parts.append(f"{role}: {content}")

        text_for_compression = "\n".join(text_parts)

        try:
            from memory.llm_compressor import LLMCompressor

            if self._llm_compressor is None:
                self._llm_compressor = LLMCompressor(self.llm_client)

            summary = await self._llm_compressor.compress(text_for_compression)

            key = f"dialogue_summary:{int(time.time())}-{uuid.uuid4().hex[:6]}"
            await self.memory.remember_dialogue_summary(
                key,
                {
                    "summary": summary,
                    "pairs_count": chunk_size // 2,
                    "compressed_at": int(time.time()),
                    "raw_preview": (
                        text_for_compression[:200] + "..."
                        if len(text_for_compression) > 200
                        else text_for_compression
                    ),
                },
            )
            logger.info(
                "Dialogue buffer compressed and stored",
                pairs=chunk_size // 2,
                summary_len=len(summary),
                key=key,
            )
        except Exception as e:
            logger.error("Failed to compress dialogue buffer", error=str(e))
            self._dialogue_buffer = messages + self._dialogue_buffer

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
                    response = {"status": "interrupted", "message": "Request cancelled by user."}
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
                elif response.get("status") == "failed":
                    print(f"\n{response.get('message', '')}")
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
        logger.info("Lazy memory compression enabled (per-request check, background run)")

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

        if self._dialogue_buffer:
            logger.info(
                "Compressing remaining dialogue buffer on shutdown",
                messages=len(self._dialogue_buffer),
            )
            await self._compress_and_store_dialogue(force=True)

        if self._compression_task and not self._compression_task.done():
            logger.info("Waiting for background memory compression to finish")
            try:
                await self._compression_task
            except Exception as e:
                logger.error("Background compression failed on shutdown", error=str(e))

        if self.watchdog:
            await self.watchdog.stop()
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
