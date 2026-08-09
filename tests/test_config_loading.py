"""Tests for Config loading priority: ENV > file > defaults (P2-1)."""

import json
from pathlib import Path

from core.config import Config


def test_defaults_when_no_file():
    config = Config.load(config_path="nonexistent_file.json")
    assert config.llm_model == "vasily-qwen"
    assert config.max_tool_calls_per_tool == 3
    assert config.dev_mode is False
    assert isinstance(config.log_dir, Path)


def test_file_overrides_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("VASILY_MAX_TOOL_CALLS_PER_TOOL", raising=False)
    monkeypatch.delenv("VASILY_DEV_MODE", raising=False)
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(
        json.dumps({"max_tool_calls_per_tool": 7, "dev_mode": True}),
        encoding="utf-8",
    )
    config = Config.load(config_path=str(cfg_file))
    assert config.max_tool_calls_per_tool == 7
    assert config.dev_mode is True
    # untouched fields keep defaults
    assert config.llm_model == "vasily-qwen"


def test_env_overrides_file(tmp_path, monkeypatch):
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(json.dumps({"max_tool_calls_per_tool": 7}), encoding="utf-8")
    monkeypatch.setenv("VASILY_MAX_TOOL_CALLS_PER_TOOL", "2")
    monkeypatch.setenv("VASILY_DEV_MODE", "true")
    config = Config.load(config_path=str(cfg_file))
    assert config.max_tool_calls_per_tool == 2
    assert config.dev_mode is True


def test_env_casts_types(monkeypatch):
    monkeypatch.setenv("VASILY_LLM_TIMEOUT", "12.5")
    monkeypatch.setenv("VASILY_MAX_REACT_ITERATIONS", "4")
    config = Config.load(config_path="nonexistent.json")
    assert config.llm_timeout == 12.5
    assert config.max_react_iterations == 4
