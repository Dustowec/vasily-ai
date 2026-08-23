"""Watchdog — фоновый мониторинг и автовосстановление модулей."""

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

logger = get_logger("core", "Watchdog")

# Сообщение пользователю при фатальном сбое LLM
LLM_CRASH_MESSAGE = (
    "[!!!ВНИМАНИЕ!!!] - ЛЛМ не доступна! Требуется перезапуск! "
    "Проверьте, запущен ли Ollama! При возникновении трудностей, "
    "обратитесь к системному администратору!"
)


class Watchdog:
    """Фоновый мониторинг и автовосстановление."""

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

        # Состояние модулей
        self.llm_available = True
        self.plugins_available = True
        self.memory_available = True
        self.disk_available = True

        # Счётчики ошибок
        self.llm_failures = 0
        self.plugins_failures = 0
        self.memory_failures = 0

    async def start(self) -> None:
        """Запустить Watchdog как фоновую задачу."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Watchdog started", interval=self.check_interval)

    async def stop(self) -> None:
        """Остановить Watchdog."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Watchdog stopped")

    async def _run(self) -> None:
        """Основной цикл Watchdog."""
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
        """Проверить все модули."""
        # 1. Проверка LLM (Ollama)
        if self.agent.llm_client:
            ok = await self._check_llm()
            if not ok:
                await self._handle_llm_failure()
            else:
                self.llm_failures = 0

        # 2. Проверка плагинов (echo)
        ok = await self._check_plugins()
        if not ok:
            await self._handle_plugins_failure()
        else:
            self.plugins_failures = 0

        # 3. Проверка памяти
        ok = await self._check_memory()
        if not ok:
            await self._handle_memory_failure()
        else:
            self.memory_failures = 0

        # 4. Проверка диска
        await self._check_disk()

    # ==================== LLM ====================

    async def _check_llm(self) -> bool:
        """Проверить доступность LLM."""
        try:
            ok = await self.agent.llm_client.health_check()
            if ok:
                if not self.llm_available:
                    logger.info("LLM восстановлен")
                    self.llm_available = True
                return True
            else:
                logger.warning("LLM недоступен")
                self.llm_available = False
                return False
        except Exception as e:
            logger.warning("LLM проверка провалилась", error=str(e))
            self.llm_available = False
            return False

    async def _handle_llm_failure(self) -> None:
        """Обработка сбоя LLM."""
        self.llm_failures += 1
        logger.warning("LLM сбой", attempt=self.llm_failures, max=self.max_restarts)

        if self.llm_failures <= self.max_restarts:
            # Попытка восстановления
            logger.info("Попытка восстановления LLM...")
            await asyncio.sleep(self.restart_timeout)

            # Пересоздаём клиент
            from integrations.ollama_client import OllamaClient

            self.agent.llm_client = OllamaClient(
                base_url=self.agent.config.llm_url,
                model=self.agent.config.llm_model,
                timeout=self.agent.config.llm_timeout,
                max_retries=self.agent.config.llm_max_retries,
                num_ctx=self.agent.config.llm_num_ctx,
                retry_delay_base=self.agent.config.llm_retry_delay_base,
            )
            # Обновляем в ReactLoop
            if self.agent.react_loop:
                self.agent.react_loop.llm = self.agent.llm_client

            # Проверяем снова
            ok = await self._check_llm()
            if ok:
                logger.info("LLM восстановлен после попытки", attempt=self.llm_failures)
                self.llm_failures = 0
                return

        # Фатальный сбой
        logger.critical("LLM недоступен после всех попыток")
        await self._report_llm_crash()

    async def _report_llm_crash(self) -> None:
        """Сгенерировать crash-репорт и уведомить пользователя."""
        from core.crash_reporter import CrashReporter

        try:
            reporter = CrashReporter(self.agent.config.log_dir)
            error = RuntimeError("LLM (Ollama) недоступен после всех попыток восстановления")
            json_path, md_path = reporter.generate_report(error, request_id="watchdog-llm")
            logger.error("Crash-репорт сгенерирован", json=str(json_path), md=str(md_path))
        except Exception as e:
            logger.error("Не удалось создать crash-репорт", error=str(e))

        # Уведомление пользователя
        print("\n" + "=" * 60)
        print(LLM_CRASH_MESSAGE)
        print("=" * 60 + "\n")

        self.llm_available = False

    # ==================== ПЛАГИНЫ ====================

    async def _check_plugins(self) -> bool:
        """Проверить доступность плагинов через echo."""
        try:
            echo_tool = self.agent.plugin_registry.get("echo")
            if not echo_tool:
                logger.warning("Плагин echo не найден")
                return False

            result = await echo_tool.execute(message="ping")
            if result.get("status") == "success":
                if not self.plugins_available:
                    logger.info("Плагины восстановлены")
                    self.plugins_available = True
                return True
            else:
                logger.warning("Плагин echo не отвечает")
                self.plugins_available = False
                return False
        except Exception as e:
            logger.warning("Проверка плагинов провалилась", error=str(e))
            self.plugins_available = False
            return False

    async def _handle_plugins_failure(self) -> None:
        """Обработка сбоя плагинов."""
        self.plugins_failures += 1
        logger.warning("Сбой плагинов", attempt=self.plugins_failures, max=self.max_restarts)

        if self.plugins_failures <= self.max_restarts:
            logger.info("Попытка перезагрузки плагинов...")
            await asyncio.sleep(self.restart_timeout)

            # Перезагружаем PluginRegistry
            self.agent.plugin_registry.discover_plugins(self.agent.config.plugins_dir)
            logger.info("PluginRegistry перезагружен")

            # Проверяем снова
            ok = await self._check_plugins()
            if ok:
                logger.info("Плагины восстановлены после попытки", attempt=self.plugins_failures)
                self.plugins_failures = 0
                return

        # Фатальный сбой плагинов
        logger.critical("Плагины недоступны после всех попыток")
        self.plugins_available = False

    # ==================== ПАМЯТЬ ====================

    async def _check_memory(self) -> bool:
        """Проверить валидность файлов памяти."""
        try:
            # Проверяем, что файлы существуют и читаются
            data_dir = Path(self.agent.config.data_dir)
            files = ["tgs_memory.json", "tg_hot_memory.json", "tg_cold_memory.json"]

            for fname in files:
                path = data_dir / fname
                if path.exists():
                    try:
                        with open(path, encoding="utf-8") as f:
                            json.load(f)
                    except (json.JSONDecodeError, OSError) as e:
                        logger.error(f"Файл памяти повреждён: {fname}", error=str(e))
                        # Пытаемся восстановить из временной копии
                        await self._restore_memory_file(path)
                        return False

            self.memory_available = True
            return True
        except Exception as e:
            logger.error("Проверка памяти провалилась", error=str(e))
            self.memory_available = False
            return False

    async def _restore_memory_file(self, path: Path) -> bool:
        """Попытаться восстановить файл памяти из временной копии."""
        temp_path = path.with_suffix(path.suffix + ".tmp")
        if temp_path.exists():
            try:
                shutil.copy(temp_path, path)
                logger.info("Файл памяти восстановлен из временной копии", file=path.name)
                return True
            except Exception as e:
                logger.error("Не удалось восстановить файл памяти", file=path.name, error=str(e))
        return False

    async def _handle_memory_failure(self) -> None:
        """Обработка сбоя памяти."""
        self.memory_failures += 1
        logger.warning("Сбой памяти", attempt=self.memory_failures, max=self.max_restarts)

        if self.memory_failures <= self.max_restarts:
            logger.info("Попытка восстановления памяти...")
            await asyncio.sleep(self.restart_timeout)

            # Пробуем перезагрузить память
            self.agent.memory._load_all()
            logger.info("Память перезагружена")

            ok = await self._check_memory()
            if ok:
                logger.info("Память восстановлена после попытки", attempt=self.memory_failures)
                self.memory_failures = 0
                return

        # Фатальный сбой памяти
        logger.critical("Память недоступна после всех попыток")
        self.memory_available = False

    # ==================== ДИСК ====================

    async def _check_disk(self) -> None:
        """Проверить свободное место на диске."""
        try:
            import shutil

            usage = shutil.disk_usage(self.agent.config.log_dir)
            free_mb = usage.free / (1024 * 1024)

            if free_mb < 500:
                logger.warning(
                    "Мало свободного места на диске",
                    free_mb=round(free_mb, 1),
                    threshold=500,
                )
                self.disk_available = False
            else:
                self.disk_available = True
        except Exception as e:
            logger.error("Проверка диска провалилась", error=str(e))

    # ==================== СТАТУС ====================

    def get_status(self) -> dict[str, Any]:
        """Получить статус всех модулей."""
        return {
            "llm": {
                "available": self.llm_available,
                "failures": self.llm_failures,
            },
            "plugins": {
                "available": self.plugins_available,
                "failures": self.plugins_failures,
            },
            "memory": {
                "available": self.memory_available,
                "failures": self.memory_failures,
            },
            "disk": {
                "available": self.disk_available,
            },
        }

    def get_status_icons(self) -> str:
        """Получить строку со статус-иконками для вывода."""

        def icon(ok: bool) -> str:
            return "🟢" if ok else "🔴"

        llm_icon = icon(self.llm_available)
        plugins_icon = icon(self.plugins_available)
        memory_icon = icon(self.memory_available)
        disk_icon = icon(self.disk_available)

        return (
            f"[LLM {llm_icon} | Плагины {plugins_icon} | Память {memory_icon} | Диск {disk_icon}]"
        )
