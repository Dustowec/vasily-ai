"""Periodic scheduler - internal timers, zero-trust to external calls (ADR-005).

All periodic work lives here as internal asyncio tasks started with the
agent. Periodic timing never depends on external requests.
"""

import asyncio
from collections.abc import Callable, Coroutine

from core.logging_config import get_logger

logger = get_logger("core", "PeriodicScheduler")


class PeriodicScheduler:
    """Owns all internal periodic tasks.

    Each task is an isolated asyncio loop: one failing task is logged but
    does not kill the scheduler or block other tasks. A task never overlaps
    with its own previous run.
    """

    def __init__(self):
        self._registry: dict[str, tuple[float, Callable[[], Coroutine]]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._started = False

    def register(
        self,
        name: str,
        interval_seconds: float,
        coro_factory: Callable[[], Coroutine],
    ) -> None:
        """Register a periodic task. Duplicate names are rejected."""
        if name in self._registry:
            raise ValueError(f"Task already registered: {name}")
        self._registry[name] = (interval_seconds, coro_factory)

    async def start(self) -> None:
        """Start all registered tasks as background asyncio tasks."""
        if self._started:
            return
        self._started = True
        for name, (interval, factory) in self._registry.items():
            self._tasks[name] = asyncio.create_task(self._run_periodic(name, interval, factory))
        logger.info("Scheduler started", tasks=list(self._registry.keys()))

    async def stop(self) -> None:
        """Gracefully stop all tasks."""
        if not self._started:
            return
        self._started = False
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        logger.info("Scheduler stopped")

    async def _run_periodic(
        self, name: str, interval: float, factory: Callable[[], Coroutine]
    ) -> None:
        """Sequential loop: next run starts only after previous finished."""
        while self._started:
            try:
                await factory()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Periodic task failed", task=name, error=str(e))
            await asyncio.sleep(interval)
