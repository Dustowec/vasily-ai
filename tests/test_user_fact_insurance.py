"""ADR-013 6.4, файл 3: user_fact и страховка forget_all (§8).

Покрывает: амнистию свежих user_fact (< 10 тиков), ротацию постаревших,
границу ровно 10 тиков, next_free_tick, точечный forget поверх амнистии,
префиксную фильтрацию амнистии, страховку поверх рестарта (total_ticks
персистентен), механику ротации всех зон, очистку очереди дистилляции.
ADR-014: точечный forget — строго физическое удаление.
"""

import pytest

from memory.manager import GradientMemory


def make_entry(
    score: float,
    *,
    value="текст",
    summary=None,
    no_compress=False,
    updated_at="2026-01-01T00:00:00",
    created_tick=0,
) -> dict:
    """Белый ящик: минимальный корректный entry для вставки в зону."""
    return {
        "value": value,
        "score": score,
        "is_cold": False,
        "no_compress": no_compress,
        "shield": False,
        "summary": summary,
        "changed_since_revival": False,
        "last_heat_tick": -1,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": updated_at,
        "created_tick": created_tick,
    }


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер: все файлы памяти — во временной папке."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


# ==================== амнистия свежих user_fact (§8) ====================


async def test_fresh_user_fact_amnestied_with_stats(m):
    """§8: свежий user_fact щадится; ответ содержит числа (rotated/amnestied/тик)."""
    await m.remember_user_fact("user_fact:fresh", "Меня зовут Алекс")
    m._hot["dialogue_summary:x"] = make_entry(15.0)  # не user_fact — под ротацию
    await m.decay()
    await m.decay()
    await m.decay()  # 3 тика: 3 < 10 — окно амнистии
    result = await m.forget_all(confirm=True)
    assert result == {"rotated": 1, "amnestied": 1, "next_free_tick": 10, "confirmed": True}
    assert "user_fact:fresh" in m._hot
    assert "dialogue_summary:x" in m._cold  # ротирован на ступень вниз, жив


async def test_old_user_fact_rotated_after_window(m):
    """§8: записи старше окна считаются подтверждённо удаляемыми."""
    await m.remember_user_fact("user_fact:old", "устаревший факт")
    m._total_ticks = 12  # 12 - 0 = 12 >= 10 — окно истекло
    result = await m.forget_all(confirm=True)
    assert result["amnestied"] == 0
    assert result["rotated"] == 1
    assert "user_fact:old" in m._cold  # 40 - 50 = -10 -> COLD доживать


async def test_amnesty_boundary_exactly_10_ticks(m):
    """§8: РОВНО 10 тиков спустя — защиты уже нет (строгое '<')."""
    m._total_ticks = 5
    await m.remember_user_fact("user_fact:b", "граничный факт")  # created_tick=5
    m._total_ticks = 15  # 15 - 5 = 10 — не < 10
    result = await m.forget_all(confirm=True)
    assert result["amnestied"] == 0
    assert "user_fact:b" in m._cold


async def test_next_free_tick_reports_first_expiry(m):
    """§8: next_free_tick = created_tick + 10 — когда можно повторить ротацию."""
    m._total_ticks = 5
    await m.remember_user_fact("user_fact:n", "факт")  # created_tick=5
    m._total_ticks = 14  # 9 elapsed — ещё амнистия
    result = await m.forget_all(confirm=True)
    assert result["amnestied"] == 1
    assert result["next_free_tick"] == 15


async def test_amnesty_only_for_user_fact_prefix(m):
    """§8: префиксная фильтрация — свежая НЕ-user_fact запись не амнистируется."""
    m._total_ticks = 1
    m._hot["dialogue_summary:d"] = make_entry(15.0, created_tick=1)  # свежая, но не user_fact
    result = await m.forget_all(confirm=True)
    assert result["amnestied"] == 0
    assert "dialogue_summary:d" in m._cold


# ==================== точечный forget поверх амнистии (§8/§14) ====================


async def test_point_forget_ignores_amnesty(m):
    """§8: амнистия НЕ распространяется на точечный 'забудь X' —
    юзер может осознанно удалить ошибочный факт в любой момент.
    ADR-014: запись физически удаляется, в COLD не отправляется."""
    await m.remember_user_fact("user_fact:pf", "ошибочный факт")
    await m.decay()  # 1 тик — окно амнистии ещё идёт
    ok = await m.forget("user_fact:pf")
    assert ok is True
    assert "user_fact:pf" not in m._hot
    assert "user_fact:pf" not in m._cold  # Физическое удаление (ADR-014)


# ==================== страховка поверх рестарта (§3.1/§8) ====================


async def test_insurance_survives_restart(m, tmp_path):
    """§10.4: страховка 10 тиков работает ПОВЕРХ рестарта:
    total_ticks персистентен, created_tick живёт в файле зоны."""
    data_dir = str(tmp_path / "data")
    m1 = GradientMemory(data_dir=data_dir)
    await m1.remember_user_fact("user_fact:pr", "переживает рестарт")
    await m1.decay()
    await m1.decay()
    await m1.decay()  # total_ticks = 3, meta.json записан

    m2 = GradientMemory(data_dir=data_dir)  # «перезапуск»
    assert m2._total_ticks == 3
    result = await m2.forget_all(confirm=True)
    assert result["amnestied"] == 1  # 3 - 0 = 3 < 10
    assert "user_fact:pr" in m2._hot


async def test_remember_user_fact_stamps_created_tick(m):
    """§8: created_tick ставится из текущего total_ticks при записи."""
    m._total_ticks = 7
    await m.remember_user_fact("user_fact:ct", "факт")
    assert m._hot["user_fact:ct"]["created_tick"] == 7


# ==================== механика полной ротации (§8) ====================


async def test_forget_all_rotates_all_zones(m):
    """§8: ступеньки вниз без сожжения: TGS -> HOT(40, без shield);
    HOT -> COLD(value=None, fallback-summary); удаляются ТОЛЬКО те,
    кто лежал в COLD до ротации (переселенцы доживают)."""
    m._tgs["k1"] = make_entry(55.0, updated_at="2026-01-02T00:00:00")
    m._tgs["k1"]["shield"] = True
    m._hot["k2"] = make_entry(30.0, value="text value")
    m._cold["k3"] = make_entry(-5.0, value=None, summary="старьё")

    result = await m.forget_all(confirm=True)
    assert result["rotated"] == 3
    assert m._hot["k1"]["score"] == pytest.approx(40.0)  # ступенька вниз, жива
    assert m._hot["k1"]["shield"] is False
    assert m._cold["k2"]["value"] is None
    assert m._cold["k2"]["summary"] == "text value"  # fallback без LLM
    assert m._cold["k2"]["score"] == pytest.approx(-20.0)  # 30 - 50
    assert "k3" not in m._cold  # старый COLD -> удаление


async def test_forget_all_clears_distill_queue(m):
    """§8: тотальная ротация отменяет отложенную дистилляцию;
    запись при этом честно доживает в COLD (а не стирается багом)."""
    m._hot["q"] = make_entry(3.0)
    m._distill_queue.append("q")
    await m.forget_all(confirm=True)
    assert m.has_compression_candidates() is False
    assert "q" in m._cold  # 3 - 50 = -47 > -50: доживает
    assert "q" not in m._hot


async def test_forget_all_requires_confirm(m):
    """§8: без подтверждения — пустая статистика, память не тронута."""
    m._hot["k"] = make_entry(15.0)
    result = await m.forget_all()
    assert result == {
        "rotated": 0,
        "amnestied": 0,
        "next_free_tick": 0,
        "confirmed": False,
    }
    assert "k" in m._hot
