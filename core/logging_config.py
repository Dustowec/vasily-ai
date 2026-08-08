"""
Structured logging configuration with 4 log levels and alert levels.

Log files (rotated every 72 hours):
- logs/core.log       - Core system logs
- logs/interaction.log - Core-to-plugin interaction logs
- logs/plugins.log    - Plugin internal logs
- logs/llm.log        - LLM request/response logs
- logs/vasily.log     - All logs combined

Alert levels (auto-detected):
- STATE             - System state (INFO by default)
- REQUEST           - User request (INFO with request keywords)
- WARNING           - Warning (WARNING)
- CRITICAL_WARNING  - Critical warning (ERROR)
- CRASH             - Fatal crash (CRITICAL)
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import structlog

# Log file names
CORE_LOG = "core.log"
INTERACTION_LOG = "interaction.log"
PLUGINS_LOG = "plugins.log"
LLM_LOG = "llm.log"
ALL_LOG = "vasily.log"

# Rotation settings: keep logs for 72 hours (3 days)
ROTATION_WHEN = "midnight"  # Rotate at midnight
ROTATION_INTERVAL = 1  # Every day
ROTATION_BACKUP_COUNT = 3  # Keep 3 days of logs


def alert_level_processor(logger, method_name, event_dict):
    """
    Automatically adds alert_level based on Python log level.
    Allows manual override if alert_level is already set.

    Alert levels:
    - STATE: System state (INFO by default)
    - REQUEST: User request (INFO with request keywords)
    - WARNING: Warning (WARNING)
    - CRITICAL_WARNING: Critical warning (ERROR)
    - CRASH: Fatal crash (CRITICAL)
    """
    # If alert_level is already set manually, keep it
    if "alert_level" in event_dict:
        return event_dict

    # Auto-detect based on level
    level = event_dict.get("level", "info")

    if level == "critical":
        event_dict["alert_level"] = "CRASH"
    elif level == "error":
        event_dict["alert_level"] = "CRITICAL_WARNING"
    elif level == "warning":
        event_dict["alert_level"] = "WARNING"
    elif level == "info":
        # Detect REQUEST by keywords in event text
        event_text = event_dict.get("event", "").lower()
        request_keywords = ["request", "user", "query", "command", "input"]
        if any(word in event_text for word in request_keywords):
            event_dict["alert_level"] = "REQUEST"
        else:
            event_dict["alert_level"] = "STATE"
    elif level == "debug":
        event_dict["alert_level"] = "DEBUG"

    return event_dict


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    json_logs: bool = True,
) -> None:
    """
    Configure structured logging with 4 log levels and rotation.

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
        alert_level_processor,  # Auto-detect alert_level
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

    # === HELPER: Create rotating file handler ===
    def create_rotating_handler(filename: str) -> TimedRotatingFileHandler:
        """Create a handler that rotates logs every 72 hours."""
        handler = TimedRotatingFileHandler(
            log_dir / filename,
            when=ROTATION_WHEN,
            interval=ROTATION_INTERVAL,
            backupCount=ROTATION_BACKUP_COUNT,
            encoding="utf-8",
        )
        formatter = structlog.stdlib.ProcessorFormatter(
            processor=file_renderer,
            foreign_pre_chain=shared_processors,
        )
        handler.setFormatter(formatter)
        handler.setLevel(log_level)
        return handler

    # === FILE HANDLERS (with rotation) ===

    core_handler = create_rotating_handler(CORE_LOG)
    interaction_handler = create_rotating_handler(INTERACTION_LOG)
    plugins_handler = create_rotating_handler(PLUGINS_LOG)
    llm_handler = create_rotating_handler(LLM_LOG)
    all_handler = create_rotating_handler(ALL_LOG)

    # === CONSOLE HANDLER ===
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=console_renderer,
        foreign_pre_chain=shared_processors,
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(log_level)

    # === LOGGER CONFIGURATION ===

    # Core logger
    core_logger = logging.getLogger("vasily.core")
    core_logger.handlers.clear()
    core_logger.addHandler(core_handler)
    core_logger.addHandler(all_handler)
    core_logger.addHandler(console_handler)
    core_logger.setLevel(log_level)
    core_logger.propagate = False

    # Interaction logger
    interaction_logger = logging.getLogger("vasily.interaction")
    interaction_logger.handlers.clear()
    interaction_logger.addHandler(interaction_handler)
    interaction_logger.addHandler(all_handler)
    interaction_logger.addHandler(console_handler)
    interaction_logger.setLevel(log_level)
    interaction_logger.propagate = False

    # Plugins logger
    plugins_logger = logging.getLogger("vasily.plugins")
    plugins_logger.handlers.clear()
    plugins_logger.addHandler(plugins_handler)
    plugins_logger.addHandler(all_handler)
    plugins_logger.addHandler(console_handler)
    plugins_logger.setLevel(log_level)
    plugins_logger.propagate = False

    # LLM logger
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
