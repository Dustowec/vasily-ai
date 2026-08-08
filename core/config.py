"""Simple configuration for Vasily AI agent."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Agent configuration with sensible defaults."""

    # Paths
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    data_dir: Path = field(default_factory=lambda: Path("data"))

    # Logging
    log_level: str = "INFO"
    json_logs: bool = True

    # LLM
    llm_url: str = "http://localhost:11434"
    llm_model: str = "llama3.2"
    llm_timeout: float = 30.0

    # Agent behavior
    max_concurrent_requests: int = 5
    request_timeout: float = 60.0

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
