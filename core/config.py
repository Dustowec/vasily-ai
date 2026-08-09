"""Simple configuration for Vasily AI agent."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Agent configuration with sensible defaults."""

    # Paths
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    data_dir: Path = field(default_factory=lambda: Path("data"))
    plugins_dir: str = "plugins"

    # Logging
    log_level: str = "INFO"
    json_logs: bool = True

    # Crash reporter
    crash_report_lines: int = 50

    # Sanitization (T3-016.5, implementation before Phase 4)
    sanitize_logs: bool = True
    max_log_field_length: int = 100

    # LLM
    llm_url: str = "http://localhost:11434"
    llm_model: str = "vasily-qwen"
    llm_timeout: float = 30.0
    llm_auto_start: bool = False
    llm_max_retries: int = 2
    llm_num_ctx: int = 32768

    # Agent behavior
    max_concurrent_requests: int = 5
    request_timeout: float = 60.0
    max_react_iterations: int = 10

    @classmethod
    def load(cls) -> "Config":
        """Load configuration (for now uses defaults)."""
        return cls()

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
        if self.crash_report_lines <= 0:
            raise ValueError("crash_report_lines must be positive")
        if self.max_log_field_length <= 0:
            raise ValueError("max_log_field_length must be positive")
        if self.max_react_iterations <= 0:
            raise ValueError("max_react_iterations must be positive")
