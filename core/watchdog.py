"""Watchdog — фоновый мониторинг и автовосстановление модулей (облегчённая версия)."""

import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

# Основной логгер (только для критических событий)
logger = get_logger("core", "Watchdog")


# Отдельный логгер для Watchdog с ротацией по числу записей
class WatchdogLogger:
    """Логгер с ротацией по числу записей (максимум 50 циклов)."""

    def __init__(self, log_dir: Path, max_entries: int = 50):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "watchdog.log"
        self.max_entries = max_entries
        self._counter = 0

    def _rotate(self):
        """Ротация при превышении max_entries."""
        if not self.log_file.exists():
            return
        try:
            with open(self.log_file, encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) >= self.max_entries:
                # Оставляем только последние 10 записей для контекста
                with open(self.log_file, "w", encoding="utf-8") as f:
                    f.writelines(lines[-10:])
        except Exception:
            pass

    def log(self, level: str, message: str, **kwargs):
        """Записать сообщение в файл."""
        self._counter += 1
        if self._counter % 10 == 0:
            self._rotate()
        try:
            timestamp = datetime.now().isoformat()
            line = f"{timestamp} [{level}] {message}"
            if kwargs:
                line += " " + " ".join(f"{k}={v}" for k, v in kwargs.items())
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# Сообщение пользователю при фатальном сбое LLM
LLM_CRASH_MESSAGE = (
    "[!!!ВНИМАНИЕ!!!] - ЛЛМ не доступна! Требуется перезапуск! "
    "Проверьте, запущен ли Ollama! При возникновении трудностей, "
    "обратитесь к системному администратору!"
)


class Watchdog:
    """Фоновый мониторинг и автовосстановление (облегчённая версия)."""

    def __init__(
        self,
        agent,
        check_interval: int = 30,
        restart_timeout: int = 5,
        max_restarts: int = 2,
    ):
        self.agent = agent
        self.check_interval = check_interval
        self.restart_timeout = restart_timeout
        self.max_restarts = max_restarts
        self._running = False
        self._task: asyncio.Task | None = None

        # Состояние модулей (только для статуса)
        self.llm_available = True
        self.plugins_available = True
        self.memory_available = True
        self.disk_available = True

        # Счётчики ошибок
        self.llm_failures = 0
        self.plugins_failures = 0
        self.memory_failures = 0

        # Режим тишины (подавление дублирующихся ошибок)
        self._llm_notified = False
        self._plugins_notified = False
        self._memory_notified = False
        self._disk_notified = False

        # Отдельный логгер
        self._wd_logger = WatchdogLogger(self.agent.config.log_dir)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Watchdog started", interval=self.check_interval)
        # Запись в отдельный лог при старте
        self._wd_logger.log("INFO", "Watchdog started", interval=self.check_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Watchdog stopped")
        self._wd_logger.log("INFO", "Watchdog stopped")

    async def _run(self) -> None:
        while self._running:
            try:
                await self._check_all()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Watchdog error", error=str(e))
                await asyncio.sleep(self.check_interval)

    async def _check_all(self) -> None:
        # 1. Проверка LLM (Ollama) — лёгкая
        if self.agent.llm_client:
            ok = await self._check_llm()
            if not ok:
                await self._handle_llm_failure()
            else:
                self.llm_failures = 0
                self._llm_notified = False

        # 2. Проверка плагинов — только наличие в реестре
        ok = await self._check_plugins()
        if not ok:
            await self._handle_plugins_failure()
        else:
            self.plugins_failures = 0
            self._plugins_notified = False

        # 3. Проверка памяти — только валидность файлов
        ok = await self._check_memory()
        if not ok:
            await self._handle_memory_failure()
        else:
            self.memory_failures = 0
            self._memory_notified = False

        # 4. Проверка диска
        await self._check_disk()

    # ==================== LLM ====================

    async def _check_llm(self) -> bool:
        try:
            ok = await self.agent.llm_client.health_check()
            if ok:
                self.llm_available = True
                return True
            else:
                self.llm_available = False
                return False
        except Exception:
            self.llm_available = False
            return False

    async def _handle_llm_failure(self) -> None:
        self.llm_failures += 1
        if self.llm_failures <= self.max_restarts:
            logger.warning("LLM недоступен, попытка восстановления", attempt=self.llm_failures)
            self._wd_logger.log(
                "WARNING", "LLM unavailable, restart attempt", attempt=self.llm_failures
            )
            await asyncio.sleep(self.restart_timeout)

            from integrations.ollama_client import OllamaClient

            self.agent.llm_client = OllamaClient(
                base_url=self.agent.config.llm_url,
                model=self.agent.config.llm_model,
                timeout=self.agent.config.llm_timeout,
                max_retries=self.agent.config.llm_max_retries,
                num_ctx=self.agent.config.llm_num_ctx,
                retry_delay_base=self.agent.config.llm_retry_delay_base,
            )
            if self.agent.react_loop:
                self.agent.react_loop.llm = self.agent.llm_client

            ok = await self._check_llm()
            if ok:
                logger.info("LLM восстановлен после попытки", attempt=self.llm_failures)
                self._wd_logger.log(
                    "INFO", "LLM recovered after attempt", attempt=self.llm_failures
                )
                self.llm_failures = 0
                return

        if not self._llm_notified:
            logger.critical("LLM недоступен после всех попыток")
            self._wd_logger.log("CRITICAL", "LLM unavailable after all attempts")
            await self._report_llm_crash()
            self._llm_notified = True

    async def _report_llm_crash(self) -> None:
        from core.crash_reporter import CrashReporter

        try:
            reporter = CrashReporter(self.agent.config.log_dir)
            error = RuntimeError("LLM (Ollama) недоступен после всех попыток восстановления")
            json_path, md_path = reporter.generate_report(error, request_id="watchdog-llm")
            logger.error("Crash-репорт сгенерирован", json=str(json_path), md=str(md_path))
        except Exception as e:
            logger.error("Не удалось создать crash-репорт", error=str(e))

        print("\n" + "=" * 60)
        print(LLM_CRASH_MESSAGE)
        print("=" * 60 + "\n")
        self.llm_available = False

    # ==================== ПЛАГИНЫ ====================

    async def _check_plugins(self) -> bool:
        # Только проверяем наличие echo в реестре
        echo_tool = self.agent.plugin_registry.get("echo")
        if echo_tool is not None:
            self.plugins_available = True
            return True
        else:
            self.plugins_available = False
            return False

    async def _handle_plugins_failure(self) -> None:
        self.plugins_failures += 1
        if self.plugins_failures <= self.max_restarts:
            logger.warning(
                "Плагины недоступны, перезагрузка реестра", attempt=self.plugins_failures
            )
            self._wd_logger.log(
                "WARNING", "Plugins unavailable, reloading registry", attempt=self.plugins_failures
            )
            await asyncio.sleep(self.restart_timeout)
            self.agent.plugin_registry.discover_plugins(self.agent.config.plugins_dir)
            ok = await self._check_plugins()
            if ok:
                logger.info("Плагины восстановлены после попытки", attempt=self.plugins_failures)
                self._wd_logger.log("INFO", "Plugins recovered", attempt=self.plugins_failures)
                self.plugins_failures = 0
                return

        if not self._plugins_notified:
            logger.critical("Плагины недоступны после всех попыток")
            self._wd_logger.log("CRITICAL", "Plugins unavailable after all attempts")
            self.plugins_available = False
            self._plugins_notified = True

    # ==================== ПАМЯТЬ ====================

    async def _check_memory(self) -> bool:
        try:
            data_dir = Path(self.agent.config.data_dir)
            files = ["tgs_memory.json", "tg_hot_memory.json", "tg_cold_memory.json"]
            for fname in files:
                path = data_dir / fname
                if path.exists():
                    try:
                        with open(path, encoding="utf-8") as f:
                            json.load(f)
                    except (json.JSONDecodeError, OSError):
                        await self._restore_memory_file(path)
                        return False
            self.memory_available = True
            return True
        except Exception:
            self.memory_available = False
            return False

    async def _restore_memory_file(self, path: Path) -> bool:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        if temp_path.exists():
            try:
                shutil.copy(temp_path, path)
                logger.info("Файл памяти восстановлен из временной копии", file=path.name)
                self._wd_logger.log("INFO", f"Memory file {path.name} restored from temp")
                return True
            except Exception as e:
                logger.error("Не удалось восстановить файл памяти", file=path.name, error=str(e))
        return False

    async def _handle_memory_failure(self) -> None:
        self.memory_failures += 1
        if self.memory_failures <= self.max_restarts:
            logger.warning("Сбой памяти, попытка перезагрузки", attempt=self.memory_failures)
            self._wd_logger.log(
                "WARNING", "Memory failure, reloading", attempt=self.memory_failures
            )
            await asyncio.sleep(self.restart_timeout)
            self.agent.memory._load_all()
            ok = await self._check_memory()
            if ok:
                logger.info("Память восстановлена после попытки", attempt=self.memory_failures)
                self._wd_logger.log("INFO", "Memory recovered", attempt=self.memory_failures)
                self.memory_failures = 0
                return

        if not self._memory_notified:
            logger.critical("Память недоступна после всех попыток")
            self._wd_logger.log("CRITICAL", "Memory unavailable after all attempts")
            self.memory_available = False
            self._memory_notified = True

    # ==================== ДИСК ====================

    async def _check_disk(self) -> None:
        try:
            usage = shutil.disk_usage(self.agent.config.log_dir)
            free_mb = usage.free / (1024 * 1024)
            if free_mb < 500:
                if not self._disk_notified:
                    logger.warning("Мало свободного места на диске", free_mb=round(free_mb, 1))
                    self._wd_logger.log("WARNING", f"Low disk space: {round(free_mb,1)} MB")
                    self._disk_notified = True
                self.disk_available = False
            else:
                self.disk_available = True
                self._disk_notified = False
        except Exception:
            pass

    # ==================== СТАТУС ====================

    def get_status(self) -> dict[str, Any]:
        return {
            "llm": {"available": self.llm_available, "failures": self.llm_failures},
            "plugins": {"available": self.plugins_available, "failures": self.plugins_failures},
            "memory": {"available": self.memory_available, "failures": self.memory_failures},
            "disk": {"available": self.disk_available},
        }

    def get_status_icons(self) -> str:
        def icon(ok: bool) -> str:
            return "🟢" if ok else "🔴"

        return f"[LLM {icon(self.llm_available)} | Плагины {icon(self.plugins_available)} | Память {icon(self.memory_available)} | Диск {icon(self.disk_available)}]"
