"""PeriodicScheduler tests (ADR-005, TZ-022)."""

import asyncio

import pytest

from core.scheduler import PeriodicScheduler


async def test_task_runs_periodically():
    scheduler = PeriodicScheduler()
    counter = {"n": 0}

    async def tick():
        counter["n"] += 1

    scheduler.register("tick", 0.05, tick)
    await scheduler.start()
    for _ in range(50):
        if counter["n"] >= 2:
            break
        await asyncio.sleep(0.02)
    await scheduler.stop()
    assert counter["n"] >= 2


async def test_task_failure_does_not_kill_scheduler():
    scheduler = PeriodicScheduler()
    good = {"n": 0}

    async def bad():
        raise RuntimeError("boom")

    async def ok():
        good["n"] += 1

    scheduler.register("bad", 0.05, bad)
    scheduler.register("ok", 0.05, ok)
    await scheduler.start()
    for _ in range(50):
        if good["n"] >= 2:
            break
        await asyncio.sleep(0.02)
    await scheduler.stop()
    assert good["n"] >= 2


async def test_stop_cancels_tasks():
    scheduler = PeriodicScheduler()
    counter = {"n": 0}

    async def tick():
        counter["n"] += 1

    scheduler.register("tick", 0.05, tick)
    await scheduler.start()
    await asyncio.sleep(0.1)
    await scheduler.stop()
    frozen = counter["n"]
    await asyncio.sleep(0.15)
    assert counter["n"] == frozen


async def test_duplicate_registration_rejected():
    scheduler = PeriodicScheduler()

    async def tick():
        pass

    scheduler.register("tick", 1.0, tick)
    with pytest.raises(ValueError):
        scheduler.register("tick", 1.0, tick)


async def test_long_task_does_not_overlap_itself():
    scheduler = PeriodicScheduler()
    state = {"active": 0, "max_active": 0}

    async def slow():
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0.12)
        state["active"] -= 1

    scheduler.register("slow", 0.02, slow)
    await scheduler.start()
    await asyncio.sleep(0.4)
    await scheduler.stop()
    assert state["max_active"] == 1
