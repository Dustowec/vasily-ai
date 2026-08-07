"""
Structured logging configuration with 4 log levels.

Log files:
- logs/core.log       - Core system logs (AgentCore, PluginRegistry, Config)
- logs/interaction.log - Core-to-plugin interaction logs
- logs/plugins.log    - Plugin internal logs
- logs/llm.log        - LLM request/response logs
- logs/vasily.log     - All logs combined (for full debugging)
"""

import logging
import sys
from pathlib import Path

import structlog

# Log file names
CORE_LOG = "core.log"
INTERACTION_LOG = "interaction.log"
PLUGINS_LOG = "plugins.log"
LLM_LOG = "llm.log"
ALL_LOG = "vasily.log"


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    json_logs: bool = True,
) -> None:
    """
    Configure structured logging with 4 log levels.

    Args:
        log_dir: Directory for log files
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
        json_logs: Use JSON format for file output
    """
    # Create log directory
    log_dir.mkdir(parents=True, exist_ok=True)

    # Determine logging level
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Shared processors for all loggers
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    # Console renderer (colorized for humans)
    console_renderer = structlog.dev.ConsoleRenderer(colors=True)

    # File renderer (JSON for machines)
    file_renderer = structlog.processors.JSONRenderer() if json_logs else console_renderer

    # Configure structlog
    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # === FILE HANDLERS ===

    # 1. Core log handler
    core_formatter = structlog.stdlib.ProcessorFormatter(
        processor=file_renderer,
        foreign_pre_chain=shared_processors,
    )
    core_handler = logging.FileHandler(log_dir / CORE_LOG, encoding="utf-8")
    core_handler.setFormatter(core_formatter)
    core_handler.setLevel(log_level)

    # 2. Interaction log handler
    interaction_formatter = structlog.stdlib.ProcessorFormatter(
        processor=file_renderer,
        foreign_pre_chain=shared_processors,
    )
    interaction_handler = logging.FileHandler(log_dir / INTERACTION_LOG, encoding="utf-8")
    interaction_handler.setFormatter(interaction_formatter)
    interaction_handler.setLevel(log_level)

    # 3. Plugins log handler
    plugins_formatter = structlog.stdlib.ProcessorFormatter(
        processor=file_renderer,
        foreign_pre_chain=shared_processors,
    )
    plugins_handler = logging.FileHandler(log_dir / PLUGINS_LOG, encoding="utf-8")
    plugins_handler.setFormatter(plugins_formatter)
    plugins_handler.setLevel(log_level)

    # 4. LLM log handler
    llm_formatter = structlog.stdlib.ProcessorFormatter(
        processor=file_renderer,
        foreign_pre_chain=shared_processors,
    )
    llm_handler = logging.FileHandler(log_dir / LLM_LOG, encoding="utf-8")
    llm_handler.setFormatter(llm_formatter)
    llm_handler.setLevel(log_level)

    # 5. All logs handler (combined)
    all_formatter = structlog.stdlib.ProcessorFormatter(
        processor=file_renderer,
        foreign_pre_chain=shared_processors,
    )
    all_handler = logging.FileHandler(log_dir / ALL_LOG, encoding="utf-8")
    all_handler.setFormatter(all_formatter)
    all_handler.setLevel(log_level)

    # === CONSOLE HANDLER ===
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=console_renderer,
        foreign_pre_chain=shared_processors,
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(log_level)

    # === LOGGER CONFIGURATION ===

    # Core logger (AgentCore, PluginRegistry, Config)
    core_logger = logging.getLogger("vasily.core")
    core_logger.handlers.clear()
    core_logger.addHandler(core_handler)
    core_logger.addHandler(all_handler)
    core_logger.addHandler(console_handler)
    core_logger.setLevel(log_level)
    core_logger.propagate = False

    # Interaction logger (core-to-plugin calls)
    interaction_logger = logging.getLogger("vasily.interaction")
    interaction_logger.handlers.clear()
    interaction_logger.addHandler(interaction_handler)
    interaction_logger.addHandler(all_handler)
    interaction_logger.addHandler(console_handler)
    interaction_logger.setLevel(log_level)
    interaction_logger.propagate = False

    # Plugins logger (plugin internals)
    plugins_logger = logging.getLogger("vasily.plugins")
    plugins_logger.handlers.clear()
    plugins_logger.addHandler(plugins_handler)
    plugins_logger.addHandler(all_handler)
    plugins_logger.addHandler(console_handler)
    plugins_logger.setLevel(log_level)
    plugins_logger.propagate = False

    # LLM logger (requests/responses)
    llm_logger = logging.getLogger("vasily.llm")
    llm_logger.handlers.clear()
    llm_logger.addHandler(llm_handler)
    llm_logger.addHandler(all_handler)
    llm_logger.addHandler(console_handler)
    llm_logger.setLevel(log_level)
    llm_logger.propagate = False


def get_logger(category: str, name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Get a logger for a specific category.

    Args:
        category: One of "core", "interaction", "plugins", "llm"
        name: Module name (automatically added to logs)

    Returns:
        Bound structlog logger

    Example:
        logger = get_logger("core", "AgentCore")
        logger.info("Agent started", plugins_count=4)
    """
    # Map category to logger name
    logger_names = {
        "core": "vasily.core",
        "interaction": "vasily.interaction",
        "plugins": "vasily.plugins",
        "llm": "vasily.llm",
    }

    logger_name = logger_names.get(category, "vasily.core")
    logger = structlog.get_logger(logger_name)

    if name:
        logger = logger.bind(module=name)

    return logger
