"""Configuration for Vasily AI agent.
Loading priority (highest to lowest):
1. Environment variables (VASILY_<FIELD> in uppercase)
2. JSON config file (path from VASILY_CONFIG env, default: vasily_config.json)
3. Defaults in the dataclass
"""

import json
import os
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

logger = get_logger("core", "Config")

DEFAULT_CONFIG_FILE = "vasily_config.json"


@dataclass
class Config:
    """Agent configuration with env + file + default resolution."""

    # Paths
    log_dir: Path = Path("logs")
    data_dir: Path = Path("data")
    plugins_dir: str = "plugins"

    # Logging
    log_level: str = "INFO"
    json_logs: bool = True

    # Crash reporter
    crash_report_lines: int = 50

    # Development mode: allow mock data from plugins
    dev_mode: bool = False

    # Sanitization
    sanitize_logs: bool = True
    max_log_field_length: int = 100
    log_sensitive_keys: list[str] = field(
        default_factory=lambda: [
            "password",
            "token",
            "authorization",
            "cookie",
            "email",
            "prompt",
            "query",
            "url",
            "content",
            "text",
            "message",
            "subject",
            "tags",
        ]
    )
    log_redact_keys: list[str] = field(
        default_factory=lambda: ["password", "token", "authorization", "cookie"]
    )

    # LLM (ADR-011: Optimized for 4B Reasoning model, strict context limits)
    llm_url: str = "http://localhost:11434"
    llm_model: str = "vasily-qwen"
    llm_timeout: float = 120.0  # ADR-011: было 30.0
    llm_auto_start: bool = False
    llm_auto_start_timeout: float = 30.0
    llm_max_retries: int = 2
    llm_num_ctx: int = 8192  # ADR-011: было 32768
    llm_safety_margin: int = 4096  # ADR-011: было 1000
    # Hard cap on LLM response length. Must fit into llm_safety_margin.
    # Covers max_thinking_tokens (2000) + room for the actual answer.
    llm_num_predict: int = 3072
    llm_retry_delay_base: float = 1.0
    repeat_penalty: float = 1.1  # ADR-011: новый параметр
    max_thinking_tokens: int = 2000  # ADR-011: новый параметр

    # Agent behavior
    max_concurrent_requests: int = 5
    request_timeout: float = 180.0  # ADR-011: было 60.0
    max_react_iterations: int = 7  # ADR-011: было 6
    max_tool_calls_per_tool: int = 3
    log_preview_length: int = 100

    # Tool execution guards (guard against runaway plugins / context flooding)
    plugin_timeout: float = 60.0
    max_tool_content_chars: int = 4000

    # Memory tools
    enable_query_expansion: bool = True

    # External backends (plugins)
    searxng_url: str = "http://localhost:8080/search"
    danbooru_url: str = "https://danbooru.donmai.us"

    # Watchdog (мониторинг и автовосстановление)
    watchdog_enabled: bool = True
    watchdog_check_interval: int = 30
    watchdog_restart_timeout: int = 5
    watchdog_max_restarts: int = 2

    @classmethod
    def load(cls, config_path: str | None = None) -> "Config":
        """Load configuration with priority: ENV > file > defaults."""
        data: dict[str, Any] = {}
        for f in fields(cls):
            if f.default is not MISSING:
                data[f.name] = f.default
            elif f.default_factory is not MISSING:
                data[f.name] = f.default_factory()

        path_str = config_path or os.environ.get("VASILY_CONFIG") or DEFAULT_CONFIG_FILE
        path = Path(path_str)
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    file_data = json.load(f)
                if isinstance(file_data, dict):
                    known = {f.name for f in fields(cls)}
                    unknown = set(file_data) - known
                    if unknown:
                        logger.warning(
                            "Unknown keys in config file (typo? key ignored)",
                            keys=sorted(unknown),
                            config_file=str(path),
                        )
                    data.update(file_data)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(
                    "Failed to read config file, using defaults + env only",
                    config_file=str(path),
                    error=str(e),
                )

        for f in fields(cls):
            env_key = f"VASILY_{f.name.upper()}"
            env_val = os.environ.get(env_key)
            if env_val is not None:
                try:
                    data[f.name] = cls._cast(env_val, f.type)
                except (ValueError, TypeError) as e:
                    logger.warning(
                        "Invalid env value ignored, using default",
                        env_key=env_key,
                        error=str(e),
                    )

        data["log_dir"] = Path(data.get("log_dir", "logs"))
        data["data_dir"] = Path(data.get("data_dir", "data"))
        return cls(**data)

    @staticmethod
    def _cast(value: str, type_hint: Any) -> Any:
        """Cast an env string to the declared field type."""
        name = getattr(type_hint, "__name__", str(type_hint))
        if name == "bool":
            return value.lower() in ("1", "true", "yes", "on")
        if name == "int":
            return int(value)
        if name == "float":
            return float(value)
        if name.startswith("list"):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as err:
                raise ValueError(f"not a valid JSON list: {value[:50]!r}") from err
            if not isinstance(parsed, list):
                raise ValueError(f"not a JSON list: {value[:50]!r}")
            return parsed
        return value

    def validate(self) -> None:
        """Validate configuration values."""
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError(f"Invalid log level: {self.log_level}")
        if self.request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        if self.llm_max_retries < 0:
            raise ValueError("llm_max_retries must be >= 0")
        if self.llm_num_ctx <= 0:
            raise ValueError("llm_num_ctx must be positive")
        if self.llm_safety_margin < 0:
            raise ValueError("llm_safety_margin must be >= 0")
        if self.llm_num_predict <= 0:
            raise ValueError("llm_num_predict must be positive")
        if self.llm_num_predict > self.llm_safety_margin:
            raise ValueError(
                "llm_num_predict must be <= llm_safety_margin: "
                "the response must fit into the reserved context margin"
            )
        if self.max_thinking_tokens <= 0:
            raise ValueError("max_thinking_tokens must be positive")
        if self.llm_retry_delay_base < 0:
            raise ValueError("llm_retry_delay_base must be >= 0")
        if self.crash_report_lines <= 0:
            raise ValueError("crash_report_lines must be positive")
        if self.max_log_field_length <= 0:
            raise ValueError("max_log_field_length must be positive")
        if self.max_react_iterations <= 0:
            raise ValueError("max_react_iterations must be positive")
        if self.max_tool_calls_per_tool <= 0:
            raise ValueError("max_tool_calls_per_tool must be positive")
        if self.log_preview_length <= 0:
            raise ValueError("log_preview_length must be positive")
        if self.plugin_timeout <= 0:
            raise ValueError("plugin_timeout must be positive")
        if self.max_tool_content_chars <= 0:
            raise ValueError("max_tool_content_chars must be positive")
