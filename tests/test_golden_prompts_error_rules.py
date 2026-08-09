"""Tests: golden prompts contain error-handling rules (T3-017.5 Step 3)."""

from core.golden_prompts import GoldenPromptsLibrary

REQUIRED_PHRASES = [
    "do not retry the same call",
    "backend_unavailable",
    "connection_failed",
    "temporarily unavailable",
]


def test_default_prompt_has_error_rules(tmp_path):
    library = GoldenPromptsLibrary(str(tmp_path / "golden.json"))
    prompt = library.get_prompt("default")
    assert prompt is not None
    for phrase in REQUIRED_PHRASES:
        assert phrase in prompt


def test_search_prompt_has_error_rules(tmp_path):
    library = GoldenPromptsLibrary(str(tmp_path / "golden.json"))
    prompt = library.get_prompt("search")
    assert prompt is not None
    for phrase in REQUIRED_PHRASES:
        assert phrase in prompt
