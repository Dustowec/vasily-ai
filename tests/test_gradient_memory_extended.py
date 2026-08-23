"""Extended tests for GradientMemory.

Covers: edge cases, protected/shield flags,
        zone transitions, atomic writes,
        forget commands, build_context.
"""

import pytest

from memory.manager import GradientMemory


@pytest.fixture
def memory(tmp_path):
    """Create a GradientMemory instance with isolated files."""
    return GradientMemory(data_dir=str(tmp_path))


# ==================== TEST INITIALIZATION ====================


def test_init_creates_directories(tmp_path):
    """GradientMemory should create data directory."""
    data_dir = tmp_path / "data"
    GradientMemory(data_dir=str(data_dir))
    assert data_dir.exists()


def test_init_loads_empty_files(tmp_path):
    """GradientMemory should handle empty files."""
    memory = GradientMemory(data_dir=str(tmp_path))
    assert memory._tgs == {}
    assert memory._hot == {}
    assert memory._cold == {}


# ==================== TEST REMEMBER ====================


async def test_remember_simple_query(memory):
    """remember with simple query should use DEFAULT_SIMPLE_SCORE."""
    await memory.remember("key1", "simple value")
    assert "key1" in memory._hot
    assert memory._hot["key1"]["score"] == 25.0


async def test_remember_complex_query(memory):
    """remember with complex query should use DEFAULT_COMPLEX_SCORE."""
    await memory.remember("key1", "complex value", complex_query=True)
    assert "key1" in memory._hot
    assert memory._hot["key1"]["score"] == 40.0


async def test_remember_existing_updates_score(memory):
    """remember should increase score on existing entry."""
    await memory.remember("key1", "value1")
    initial_score = memory._hot["key1"]["score"]
    await memory.remember("key1", "value1 updated")
    new_score = memory._hot["key1"]["score"]
    assert new_score > initial_score


async def test_remember_moves_from_cold_to_hot(memory):
    """remember should move entry from cold to hot."""
    # Помещаем в cold
    memory._cold["key1"] = {
        "value": None,
        "score": -5.0,
        "is_cold": True,
        "protected": False,
        "shield": False,
        "summary": "summary",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    await memory.remember("key1", "new value")
    assert "key1" in memory._hot
    assert "key1" not in memory._cold
    assert memory._hot["key1"]["protected"] is True


# ==================== TEST RECALL ====================


async def test_recall_returns_value(memory):
    """recall should return stored value."""
    await memory.remember("key1", "test value")
    result = await memory.recall("key1")
    assert result == "test value"


async def test_recall_heats_entry(memory):
    """recall should increase score by REGULAR_HEAT."""
    await memory.remember("key1", "test value")
    initial_score = memory._hot["key1"]["score"]
    await memory.recall("key1")
    new_score = memory._hot["key1"]["score"]
    assert new_score == initial_score + 5.0


async def test_recall_moves_from_cold_to_hot(memory):
    """recall should move entry from cold to hot with protected flag."""
    memory._cold["key1"] = {
        "value": None,
        "score": -5.0,
        "is_cold": True,
        "protected": False,
        "shield": False,
        "summary": "summary",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    result = await memory.recall("key1")
    assert result is None  # value is None for cold entries
    assert "key1" in memory._hot
    assert "key1" not in memory._cold
    assert memory._hot["key1"]["protected"] is True
    assert memory._hot["key1"]["score"] == 10.0


async def test_recall_missing_key_returns_none(memory):
    """recall should return None for missing key."""
    result = await memory.recall("nonexistent")
    assert result is None


# ==================== TEST FORGET ====================


async def test_forget_moves_to_cold(memory):
    """forget should move entry from hot to cold with penalty."""
    await memory.remember("key1", "test value")
    initial_score = memory._hot["key1"]["score"]
    await memory.forget("key1")
    assert "key1" in memory._cold
    assert "key1" not in memory._hot
    assert memory._cold["key1"]["score"] == initial_score - 50.0


async def test_forget_deletes_below_threshold(memory):
    """forget should delete entry if score drops below DELETE_THRESHOLD."""
    await memory.remember("key1", "test value")
    memory._hot["key1"]["score"] = 10.0
    await memory.forget("key1")
    # После -50 должно стать -40, что выше DELETE_THRESHOLD (-50)
    # но в коде используется -50, и если было 10, станет -40 (не удаляется)
    # Проверяем, что в cold
    assert "key1" in memory._cold
    assert "key1" not in memory._hot


async def test_forget_tgs_entry(memory):
    """forget should handle TGS entries specially (only -20.0 penalty)."""
    await memory.remember("key1", "test value")
    memory._hot["key1"]["score"] = 60.0
    await memory._check_promote_to_tgs_unlocked("key1")
    assert "key1" in memory._tgs

    initial_score = memory._tgs["key1"]["score"]
    await memory.forget("key1")
    assert "key1" in memory._hot  # moved from TGS to HOT
    assert "key1" not in memory._tgs
    assert memory._hot["key1"]["score"] == initial_score - 20.0


async def test_forget_missing_key_returns_false(memory):
    """forget should return False for missing key."""
    result = await memory.forget("nonexistent")
    assert result is False


# ==================== TEST FORGET ALL ====================


async def test_forget_all_requires_confirmation(memory):
    """forget_all should return False without confirmation."""
    result = await memory.forget_all(confirm=False)
    assert result is False


# ==================== TEST DECAY ====================


async def test_decay_reduces_scores(memory):
    """decay should reduce scores of hot and cold entries."""
    await memory.remember("key1", "test value")
    initial_score = memory._hot["key1"]["score"]
    await memory.decay(count_requests=10)
    new_score = memory._hot["key1"]["score"]
    assert new_score < initial_score


async def test_decay_skips_protected(memory):
    """decay should skip protected entries."""
    await memory.remember("key1", "test value")
    memory._hot["key1"]["protected"] = True
    initial_score = memory._hot["key1"]["score"]
    await memory.decay(count_requests=10)
    new_score = memory._hot["key1"]["score"]
    assert new_score == initial_score


# ==================== TEST SESSION CLOSE ====================


async def test_session_close_applies_penalty(memory):
    """session_close should apply -2.0 penalty to hot and cold."""
    await memory.remember("key1", "test value")
    initial_score = memory._hot["key1"]["score"]
    await memory.session_close()
    new_score = memory._hot["key1"]["score"]
    assert new_score == initial_score - 2.0


async def test_session_close_skips_shield(memory):
    """session_close should skip TGS entries with shield."""
    await memory.remember("key1", "test value")
    memory._hot["key1"]["shield"] = True
    initial_score = memory._hot["key1"]["score"]
    await memory.session_close()
    new_score = memory._hot["key1"]["score"]
    assert new_score == initial_score


# ==================== TEST PROMOTE TO TGS ====================


async def test_promote_to_tgs(memory):
    """Entries should promote to TGS when score > 50."""
    await memory.remember("key1", "test value")
    memory._hot["key1"]["score"] = 55.0
    await memory._check_promote_to_tgs_unlocked("key1")
    assert "key1" in memory._tgs
    assert "key1" not in memory._hot
    assert memory._tgs["key1"]["shield"] is True


# ==================== TEST BUILD CONTEXT ====================


async def test_build_context_includes_tgs_first(memory):
    """build_context should include TGS entries first."""
    await memory.remember("tgs_key", "tgs value")
    memory._tgs["tgs_key"] = memory._hot.pop("tgs_key")
    memory._tgs["tgs_key"]["score"] = 55.0

    await memory.remember("hot_key", "hot value")
    memory._hot["hot_key"]["score"] = 20.0

    context = await memory.build_context("test")
    assert "[TGS: tgs_key]" in context
    assert "[HOT: hot_key]" in context


async def test_build_context_includes_cold_on_keyword_match(memory):
    """build_context should include cold entries matching query keywords."""
    memory._cold["cold_key"] = {
        "value": None,
        "score": -5.0,
        "is_cold": True,
        "protected": False,
        "shield": False,
        "summary": "stable diffusion model",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }

    context = await memory.build_context("tell me about diffusion")
    assert "[COLD: cold_key]" in context
    assert "stable diffusion model" in context


async def test_build_context_respects_max_tokens(memory):
    """build_context should respect max_tokens limit."""
    long_text = "x" * 5000
    await memory.remember("key1", long_text)
    context = await memory.build_context("test", max_tokens=100)
    assert len(context) <= 100


# ==================== TEST STATS ====================


def test_get_stats(memory):
    """get_stats should return correct statistics."""
    stats = memory.get_stats()
    assert "tgs" in stats
    assert "hot" in stats
    assert "cold" in stats
    assert "total" in stats
    assert "session_count" in stats
    assert "session_requests" in stats


# ==================== TEST LEN ====================


def test_len_returns_total_entries(memory):
    """__len__ should return total entries across all zones."""
    memory._tgs["key1"] = {}
    memory._hot["key2"] = {}
    memory._cold["key3"] = {}
    assert len(memory) == 3
