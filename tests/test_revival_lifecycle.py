"""ADR-013 6.4, файл 2: дистилляция (§5) и воскрешение (§6).

Покрывает: классификацию после стартового −2 (§3), событийную очередь
и пере-валидацию (§5), три фазы compress_cycle, траекторию
«разведка/прописка» (§6), эмерджентный remember-вход из глубокого COLD,
семантику changed_since_revival, heat_facts-revival, поиск по summary (§9).
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


async def async_compress(value) -> str:
    return f"саммари: {value}"


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер: все файлы памяти — во временной папке."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


# ==================== §3: классификация после стартового −2 ====================


async def test_cold_start_unprotected_goes_to_queue(m):
    """§3/§5: unprotected 1.5 после −2 = −0.5 <= 5.0 -> очередь дистилляции."""
    m._hot["u"] = make_entry(1.5)
    await m.cold_start_penalty()
    assert m._hot["u"]["score"] == pytest.approx(-0.5)
    assert "u" in m._distill_queue
    assert m._hot["u"]["no_compress"] is False


async def test_cold_start_no_compress_migrates_as_is(m):
    """§3/§5: no_compress 1.5 -> ловушка -> COLD(−1.2), value целиком, без LLM."""
    m._hot["n"] = make_entry(1.5, no_compress=True, value="целый текст")
    await m.cold_start_penalty()
    assert "n" in m._cold
    assert m._cold["n"]["score"] == pytest.approx(-1.2)  # −0.5 − 0.7
    assert m._cold["n"]["value"] == "целый текст"
    assert m._cold["n"]["summary"] is None


async def test_cold_start_queue_not_duplicated(m):
    """§5: повторный старт не плодит дубли в очереди."""
    m._hot["u"] = make_entry(1.5)
    await m.cold_start_penalty()
    await m.cold_start_penalty()
    assert m._distill_queue.count("u") == 1


# ==================== §5: событийная очередь ====================


async def test_event_queue_on_first_crossing_of_5(m):
    """§5: впервые опустившаяся <= 5.0 попадает в очередь немедленно."""
    m._hot["k"] = make_entry(5.6)
    await m.decay()  # 5.3 — ещё выше порога
    assert "k" not in m._distill_queue
    await m.decay()  # 5.0 <= 5.0 — сразу в очереди
    assert "k" in m._distill_queue
    assert m.has_compression_candidates() is True


# ==================== §5: пере-валидация при исполнении ====================


async def test_revalidation_skips_heated_entry(m):
    """§5: score > 5.0 на момент исполнения — вычёркивается без дистилляции."""
    m._hot["k"] = make_entry(7.0)
    m._distill_queue.append("k")
    compressed = await m.compress_cycle(async_compress)
    assert compressed == 0
    assert "k" in m._hot
    assert "k" not in m._cold


async def test_revalidation_skips_no_compress_entry(m):
    """§5: no_compress на момент исполнения — вычёркивается."""
    m._hot["k"] = make_entry(3.0, no_compress=True)
    m._distill_queue.append("k")
    compressed = await m.compress_cycle(async_compress)
    assert compressed == 0
    assert "k" in m._hot
    assert "k" not in m._cold


# ==================== §5: compress_cycle ====================


async def test_compress_cycle_distills_to_cold_minus_5(m):
    """§5/§6: успех -> COLD с фиксом −5.0, value -> None, содержимое = summary."""
    m._hot["k"] = make_entry(3.0, value="длинный текст факта")
    m._distill_queue.append("k")
    compressed = await m.compress_cycle(async_compress)
    assert compressed == 1
    assert "k" not in m._hot
    assert "k" in m._cold
    assert m._cold["k"]["score"] == pytest.approx(-5.0)
    assert m._cold["k"]["value"] is None
    assert m._cold["k"]["summary"] == "саммари: длинный текст факта"
    assert m._cold["k"]["is_cold"] is True


async def test_compress_cycle_skips_entry_replaced_during_llm(m):
    """§5: запись удалена/заменена, пока LLM думал -> не дистиллируется."""
    m._hot["k"] = make_entry(3.0, value="текст")
    m._distill_queue.append("k")

    async def replacing_compressor(value):
        m._hot.pop("k")  # запись исчезла, пока «LLM думает»
        return "саммари"

    compressed = await m.compress_cycle(replacing_compressor)
    assert compressed == 0
    assert "k" not in m._hot
    assert "k" not in m._cold


async def test_compressor_failure_postpones_and_requeues(m):
    """§5: компрессор сдался -> запись жива, флаг снят, без зомби:
    следующий тик снова ставит в очередь (самозаживление)."""
    m._hot["k"] = make_entry(3.0, value="текст")
    m._distill_queue.append("k")

    async def failing_compressor(value):
        return ""

    compressed = await m.compress_cycle(failing_compressor)
    assert compressed == 0
    assert "k" in m._hot
    assert "k" not in m._cold
    assert m._hot["k"]["value"] == "текст"
    assert "compressing" not in m._hot["k"]
    await m.decay()  # 2.7 <= 5.0 -> очередь снова
    assert "k" in m._distill_queue


# ==================== §6: траектория «разведка/прописка» ====================


async def test_revival_trajectory_reconnaissance_and_settling(m):
    """§6: COLD(−5) -> recall 2.0 -> 5 тиков -> ловушка -> COLD(−0.2)
    -> recall 4.8 («прописка»). Все числа трассируемы."""
    m._cold["k"] = make_entry(-5.0, value=None, summary="сжатое саммари")
    got = await m.recall("k")
    assert got is None  # value не восстанавливается: содержимое — summary
    assert "k" in m._hot
    assert m._hot["k"]["score"] == pytest.approx(2.0)
    assert m._hot["k"]["no_compress"] is True
    assert m._hot["k"]["changed_since_revival"] is False

    for expected in (1.7, 1.4, 1.1, 0.8):  # базовый режим −0.3
        await m.decay()
        assert m._hot["k"]["score"] == pytest.approx(expected)

    await m.decay()  # 0.5 -> ловушка -> 0.5 − 0.7 = −0.2
    assert "k" in m._cold
    assert m._cold["k"]["score"] == pytest.approx(-0.2)
    assert m._cold["k"]["value"] is None
    assert m._cold["k"]["summary"] == "сжатое саммари"  # as-is, без повторной дистилляции

    await m.recall("k")  # прописка: max(−0.2 + 5, 2.0) = 4.8
    assert "k" in m._hot
    assert m._hot["k"]["score"] == pytest.approx(4.8)


async def test_remember_from_shallow_cold_releases_flag(m):
    """§6: remember из COLD(−0.2) -> 9.8, изменён -> флаг снят, value восстановлен."""
    m._cold["k"] = make_entry(-0.2, value=None, summary="с", no_compress=True)
    await m.remember("k", "полный текст")
    assert m._hot["k"]["score"] == pytest.approx(9.8)  # max(−0.2+10, 2.0)
    assert m._hot["k"]["value"] == "полный текст"
    assert m._hot["k"]["no_compress"] is False  # 9.8 >= 8 И изменён
    assert m._hot["k"]["changed_since_revival"] is True


async def test_emergent_remember_from_deep_cold_self_resolves(m):
    """§6: remember из глубокого COLD(−5) даёт 5.0 < 8 — флаг держится,
    саморазрешается за одно касание. (9.7, а не ровно 10: между remember
    и recall обязан пройти тик — §4 «один нагрев за тик»; семантика цела.)"""
    m._cold["k"] = make_entry(-5.0, value=None, summary="с")
    await m.remember("k", "текст")
    assert m._hot["k"]["score"] == pytest.approx(5.0)
    assert m._hot["k"]["no_compress"] is True  # 5.0 < 8 — держится
    assert m._hot["k"]["changed_since_revival"] is True

    await m.decay()  # 4.7
    await m.recall("k")  # 4.7 + 5 = 9.7 >= 8 при changed=True -> снят
    assert m._hot["k"]["no_compress"] is False
    assert m._hot["k"]["score"] == pytest.approx(9.7)


async def test_recall_only_never_releases_no_compress(m):
    """§6: changed_since_revival=False — флаг не снимается, даже когда
    score давно >= 8: снимать нечего, текст не менялся."""
    m._cold["k"] = make_entry(-5.0, value=None, summary="с")
    await m.recall("k")  # 2.0
    await m.decay()  # 1.7
    await m.recall("k")  # 6.7 < 8
    await m.decay()  # 6.4
    await m.recall("k")  # 11.4 >= 8, но changed=False
    assert m._hot["k"]["score"] == pytest.approx(11.4)
    assert m._hot["k"]["no_compress"] is True


# ==================== §4/§9: heat_facts и поиск ====================


async def test_heat_facts_revives_cold_find(m):
    """§4/§6/§9: находка из COLD, попавшая в ответ агенту, воскрешается
    по общим правилам revival."""
    m._cold["k"] = make_entry(-0.5, value="найденный факт")
    await m.heat_facts(["k"])
    assert "k" not in m._cold
    assert "k" in m._hot
    assert m._hot["k"]["score"] == pytest.approx(4.5)  # max(−0.5+5, 2.0)
    assert m._hot["k"]["no_compress"] is True
    assert m._hot["k"]["is_cold"] is False


async def test_recall_memory_finds_distilled_by_summary(m):
    """§9: поиск по summary во всех зонах; зоны-призраки недопустимы:
    после воскрешения запись по-прежнему находится (уже в HOT)."""
    m._cold["k"] = make_entry(-5.0, value=None, summary="Котофей жил в 1600 году")
    found = await m.recall_memory("Котофей")
    assert found["found"] is True
    assert found["facts"][0]["key"] == "k"
    assert found["facts"][0]["zone"] == "cold"

    await m.recall("k")  # воскрешение
    found = await m.recall_memory("Котофей")
    assert found["facts"][0]["zone"] == "hot"


# ==================== сервисное ====================


async def test_remember_dialogue_summary_enters_at_15(m):
    """Диалоговые саммари входят с низким приоритетом 15 (не 40)."""
    await m.remember_dialogue_summary("d1", {"summary": "х"})
    assert m._hot["d1"]["score"] == pytest.approx(15.0)
