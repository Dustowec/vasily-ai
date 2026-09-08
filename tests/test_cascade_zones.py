"""ADR-013 6.4, файл 1: зоны, TGS, нагрев, остывание.

Покрывает: лестницу промоции и потолок 60 (§4/§7), TGS decay и демоцию (§7),
LRU-вытеснение + снятие shield (§7), стартовое охлаждение −2 (§3),
decay-таблицу и активный режим (§4.1), «один нагрев за тик» (§4),
удаление ниже −50 (§2), get_stats (§3.1/§5).
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


# ==================== лестница промоции (§4/§7) ====================


async def test_promotion_ladder_47_plus_5_to_tgs(m):
    """§4: HOT выше 49.9 -> TGS, вход со скором как есть (52)."""
    m._hot["k"] = make_entry(47.0)
    got = await m.recall("k")  # 47 + 5 = 52
    assert got == "текст"
    assert "k" not in m._hot
    assert "k" in m._tgs
    assert m._tgs["k"]["score"] == pytest.approx(52.0)
    assert m._tgs["k"]["shield"] is True


async def test_heat_capped_at_global_ceiling_60(m):
    """§4: глобальный потолок 60.0 (дефект ADR-012 «кап 100» закрыт)."""
    m._hot["k"] = make_entry(58.0)
    await m.recall("k")  # 58 + 5 = 63 -> cap 60
    assert m._tgs["k"]["score"] == pytest.approx(60.0)


async def test_tgs_heat_capped_at_60(m):
    """§7: внутри TGS нагрев капится 60, записи не покидают зону."""
    m._tgs["k"] = make_entry(58.0)
    await m.recall("k")
    assert m._tgs["k"]["score"] == pytest.approx(60.0)
    assert "k" in m._tgs


# ==================== TGS: decay, демоция, старт (§3/§7) ====================


async def test_tgs_decay_then_demote_to_hot_40(m):
    """§7: −0.1/тик; ниже 50.0 -> HOT(40), shield снят."""
    m._tgs["k"] = make_entry(50.2)
    await m.decay()  # 50.1
    assert m._tgs["k"]["score"] == pytest.approx(50.1)
    await m.decay()  # 50.0 — держится
    assert "k" in m._tgs
    await m.decay()  # 49.9 < 50.0 -> демоция; в тот же тик остынет как HOT
    assert "k" in m._hot
    assert m._hot["k"]["shield"] is False
    assert m._hot["k"]["score"] == pytest.approx(39.7)  # 40 − 0.3 того же тика


async def test_cold_start_penalty_spares_tgs(m):
    """§3: стартовый −2.0 достаётся HOT и COLD, TGS не трогается."""
    m._tgs["t"] = make_entry(55.0)
    m._hot["h"] = make_entry(15.0)
    m._cold["c"] = make_entry(-5.0)
    await m.cold_start_penalty()
    assert m._tgs["t"]["score"] == pytest.approx(55.0)
    assert m._hot["h"]["score"] == pytest.approx(13.0)
    assert m._cold["c"]["score"] == pytest.approx(-7.0)


# ==================== LRU-вытеснение (§7) ====================


async def test_tgs_overflow_evicts_lru_to_hot_40(m):
    """§7: 11-я промоция выталкивает минимальный updated_at -> HOT(40)."""
    for i in range(10):
        m._tgs[f"t{i}"] = make_entry(55.0, updated_at=f"2026-01-01T00:00:{i:02d}")
    m._hot["new"] = make_entry(50.5)
    await m.recall("new")  # 55.5 -> промоция, переполнение
    assert len(m._tgs) == 10
    assert "new" in m._tgs
    assert "t0" in m._hot  # самый старый updated_at
    assert m._hot["t0"]["score"] == pytest.approx(40.0)
    assert m._hot["t0"]["shield"] is False


async def test_no_compress_survives_tgs_roundtrip(m):
    """§7: no_compress переживает eviction; миграция as-is, LLM не звался."""
    m._tgs["nc"] = make_entry(
        55.0,
        no_compress=True,
        value="важный текст целиком",
        updated_at="2026-01-01T00:00:00",
    )
    for i in range(9):
        m._tgs[f"t{i}"] = make_entry(55.0, updated_at=f"2026-01-02T00:00:{i:02d}")
    m._hot["new"] = make_entry(50.5)
    await m.recall("new")  # nc вытеснен как самый старый
    assert "nc" in m._hot
    assert m._hot["nc"]["no_compress"] is True
    m._hot["nc"]["score"] = 1.0  # форсируем дорогу к ловушке
    await m.decay()  # 0.7
    await m.decay()  # 0.4 <= 0.5 -> ловушка -> миграция as-is
    assert "nc" in m._cold
    assert m._cold["nc"]["value"] == "важный текст целиком"
    assert m._cold["nc"]["summary"] is None  # без дистилляции


async def test_remember_in_tgs_releases_no_compress(m):
    """§7/§6: remember = изменение; score >= 8 -> флаг снят автоматически."""
    m._tgs["k"] = make_entry(55.0, no_compress=True)
    await m.remember("k", "обновлённый текст")  # 55 + 10 -> 60
    assert m._tgs["k"]["no_compress"] is False
    assert m._tgs["k"]["changed_since_revival"] is True
    assert m._tgs["k"]["value"] == "обновлённый текст"


# ==================== decay и активный режим (§4.1) ====================


async def test_hot_decay_base_rate(m):
    m._hot["k"] = make_entry(15.0)
    await m.decay()
    assert m._hot["k"]["score"] == pytest.approx(14.7)  # −0.3


async def test_active_mode_rates_and_reset_by_search(m):
    """§4.1: 10 тиков без поиска -> активный режим; поиск сбрасывает."""
    m._hot["k"] = make_entry(15.0)
    m._cold["c"] = make_entry(-5.0)
    m._ticks_since_recall = 9
    await m.decay()  # активный: HOT −0.7, COLD −1.5
    assert m._hot["k"]["score"] == pytest.approx(14.3)
    assert m._cold["c"]["score"] == pytest.approx(-6.5)
    await m.recall_memory("запрос")  # сброс счётчика
    await m.decay()  # базовый: HOT −0.3, COLD −0.5
    assert m._hot["k"]["score"] == pytest.approx(14.0)
    assert m._cold["c"]["score"] == pytest.approx(-7.0)


async def test_cold_entry_dies_below_minus_50(m):
    """§2: удаление при score ниже −50.0; COLD(−49.6) умирает за тик."""
    m._cold["k"] = make_entry(-49.6)
    await m.decay()  # −50.1
    assert "k" not in m._cold


# ==================== «один нагрев за тик» (§4) ====================


async def test_one_heat_per_tick(m):
    """§4: второй нагрев в том же тике деградирует до обновления updated_at."""
    await m.remember("k", "v1", complex_query=True)  # создание = нагрев тика
    assert m._hot["k"]["score"] == pytest.approx(40.0)
    await m.recall("k")  # тот же тик -> подавлен
    assert m._hot["k"]["score"] == pytest.approx(40.0)
    await m.decay()  # тик 1: 39.7
    await m.recall("k")  # +5 -> 44.7
    await m.remember("k", "v2")  # тот же тик -> подавлен (осознанный эффект)
    assert m._hot["k"]["score"] == pytest.approx(44.7)
    assert m._hot["k"]["value"] == "v2"  # текст свежий, скор — нет


# ==================== сервисное (§3.1) ====================


async def test_get_stats_exposes_cascade_fields(m):
    assert m.get_stats()["total_ticks"] == 0
    await m.decay()
    stats = m.get_stats()
    assert stats["total_ticks"] == 1
    assert stats["distill_queue"] == 0
    assert stats["active_mode"] is False


def test_build_context_removed(m):
    """§9/§10.1: build_context удалён как мёртвый код; инжект-канала нет."""
    assert not hasattr(m, "build_context")
