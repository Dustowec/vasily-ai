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

Sanitizer (2026-09 revision):
- Explicit redact keys (from Config.log_redact_keys) -> [REDACTED]
- Explicit sensitive keys (from Config.log_sensitive_keys) ->
    truncate on INFO/WARNING, hash+length on ERROR/CRITICAL
- Key-substring patterns (SECRET_KEY_PATTERNS) -> [REDACTED] even if
  the key is not present in Config.log_redact_keys. Catches non-standard
  secret names like 'api_key', 'secret', 'access_token', 'refresh_token'.
- Any nested dict/list is inspected recursively, regardless of the
  top-level key name ('args', 'kwargs', 'payload', 'data', ...), so
  secrets cannot leak through containers.
"""

import hashlib
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

# Log file names
CORE_LOG = "core.log"
INTERACTION_LOG = "interaction.log"
PLUGINS_LOG = "plugins.log"
LLM_LOG = "llm.log"
ALL_LOG = "vasily.log"

# Rotation settings: keep logs for 72 hours (3 days)
ROTATION_WHEN = "midnight"
ROTATION_INTERVAL = 1
ROTATION_BACKUP_COUNT = 3

# Substring patterns for key names that should always be redacted.
# Case-insensitive. Deliberately over-broad: better to over-redact a log
# field than to leak a secret under a non-standard key name.
SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "bearer",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "authorization",
    "cookie",
    "credential",
)

# --- Глобальное состояние для ленивой инициализации ---
_logging_initialized = False
_logger_proxies: dict[str, "LazyLogger"] = {}


class LazyLogger:
    """
    Прокси-логгер: перехватывает ЛЮБОЙ вызов и перенаправляет
    реальному structlog.BoundLogger после инициализации.
    """

    def __init__(self, category: str, name: str | None = None):
        self.category = category
        self.name = name
        self._logger: structlog.stdlib.BoundLogger | None = None

    def _get_real(self) -> structlog.stdlib.BoundLogger:
        """Получить реальный логгер, создав его при первом вызове."""
        if self._logger is None:
            logger_names = {
                "core": "vasily.core",
                "interaction": "vasily.interaction",
                "plugins": "vasily.plugins",
                "llm": "vasily.llm",
            }
            logger_name = logger_names.get(self.category, "vasily.core")
            self._logger = structlog.get_logger(logger_name)
            if self.name:
                self._logger = self._logger.bind(module=self.name)
        return self._logger

    def __getattr__(self, name: str) -> Any:
        """
        Перехватывает ЛЮБОЙ вызов метода (bind, exception, log, ...)
        и перенаправляет реальному логгеру.
        """
        real = self._get_real()
        attr = getattr(real, name)
        if callable(attr):

            def wrapper(*args, **kwargs):
                return attr(*args, **kwargs)

            return wrapper
        return attr

    def __repr__(self) -> str:
        return f"<LazyLogger category={self.category} name={self.name}>"


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
    if "alert_level" in event_dict:
        return event_dict

    level = event_dict.get("level", "info")

    if level == "critical":
        event_dict["alert_level"] = "CRASH"
    elif level == "error":
        event_dict["alert_level"] = "CRITICAL_WARNING"
    elif level == "warning":
        event_dict["alert_level"] = "WARNING"
    elif level == "info":
        event_text = event_dict.get("event", "").lower()
        request_keywords = ["request", "user", "query", "command", "input"]
        if any(word in event_text for word in request_keywords):
            event_dict["alert_level"] = "REQUEST"
        else:
            event_dict["alert_level"] = "STATE"
    elif level == "debug":
        event_dict["alert_level"] = "DEBUG"

    return event_dict


_SANITIZE_CONFIG_CACHE = None


def get_sanitize_config():
    global _SANITIZE_CONFIG_CACHE
    if _SANITIZE_CONFIG_CACHE is None:
        from core.config import Config

        _SANITIZE_CONFIG_CACHE = Config.load()
    return _SANITIZE_CONFIG_CACHE


def reset_sanitize_config_cache() -> None:
    global _SANITIZE_CONFIG_CACHE
    _SANITIZE_CONFIG_CACHE = None


def _key_is_secret(key: str, redact_keys: set[str]) -> bool:
    """
    Return True if the key name should be treated as a secret.

    Checks two sources:
    1. Explicit entries from Config.log_redact_keys (exact, case-insensitive).
    2. Substring patterns in SECRET_KEY_PATTERNS (case-insensitive) —
       catches non-standard names like 'api_key', 'refresh_token',
       'user_password', 'x_authorization', etc.
    """
    k = key.lower()
    if k in redact_keys:
        return True
    return any(pattern in k for pattern in SECRET_KEY_PATTERNS)


def sanitize_processor(logger, method_name, event_dict):
    """
    Sanitize sensitive fields based on log level and config.

    T3-016.5 / P3-1:
    - Critical keys (password, token, authorization, cookie, and any key
      matching SECRET_KEY_PATTERNS) -> [REDACTED]
    - Sensitive keys (prompt, query, url, content, ...) ->
        - INFO/WARNING/DEBUG: truncate to max_log_field_length
        - ERROR/CRITICAL: replace with metadata {length, hash}
    - Nested dicts/lists are inspected recursively, so secrets cannot
      hide inside 'args', 'kwargs', 'payload', etc.
    - Respects sanitize_logs=False (dev/debug mode)
    """
    config = get_sanitize_config()
    if not config.sanitize_logs:
        return event_dict

    level = event_dict.get("level", "info").lower()
    redact_keys = {k.lower() for k in config.log_redact_keys}
    sensitive_keys = {k.lower() for k in config.log_sensitive_keys}

    system_keys = {
        "timestamp",
        "level",
        "module",
        "logger",
        "request_id",
        "event",
        "exc_info",
        "alert_level",
    }

    for key in list(event_dict.keys()):
        if key in system_keys:
            continue
        event_dict[key] = _sanitize_field(
            key, event_dict[key], level, sensitive_keys, redact_keys, config
        )

    return event_dict


def _sanitize_field(key, value, level, sensitive_keys, redact_keys, config):
    """
    Decide how to treat a single (key, value) pair.

    Priority:
    1. Secret-looking key -> [REDACTED] (no exceptions).
    2. Sensitive key -> truncate/hash by level.
    3. Container value (dict/list) -> recurse, regardless of the key name.
    4. Anything else -> pass through unchanged.
    """
    k = key.lower() if isinstance(key, str) else ""

    if k and _key_is_secret(k, redact_keys):
        return "[REDACTED]"

    if k in sensitive_keys:
        return _sanitize_value(value, level, sensitive_keys, redact_keys, config)

    # Recurse into containers under any key name, to catch nested secrets.
    if isinstance(value, (dict, list, tuple)):
        return _sanitize_value(value, level, sensitive_keys, redact_keys, config)

    return value


def _sanitize_value(value, level, sensitive_keys, redact_keys, config):
    """
    Sanitize a value whose key already matched 'sensitive' or that is a
    container being recursed into.
    """
    if isinstance(value, str):
        if level in ("error", "critical"):
            return {
                "length": len(value),
                "hash": hashlib.sha256(
                    value.encode("utf-8", errors="ignore")
                ).hexdigest()[:8],
            }
        if len(value) > config.max_log_field_length:
            return value[: config.max_log_field_length] + "..."
        return value

    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            result[k] = _sanitize_field(
                k, v, level, sensitive_keys, redact_keys, config
            )
        return result

    if isinstance(value, (list, tuple)):
        return [
            _sanitize_value(v, level, sensitive_keys, redact_keys, config)
            for v in value
        ]

    return value


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
    global _logging_initialized

    log_dir.mkdir(parents=True, exist_ok=True)
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        alert_level_processor,
        sanitize_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    console_renderer = structlog.dev.ConsoleRenderer(colors=True)
    file_renderer = (
        structlog.processors.JSONRenderer() if json_logs else console_renderer
    )

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    def create_rotating_handler(filename: str) -> TimedRotatingFileHandler:
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

    core_handler = create_rotating_handler(CORE_LOG)
    interaction_handler = create_rotating_handler(INTERACTION_LOG)
    plugins_handler = create_rotating_handler(PLUGINS_LOG)
    llm_handler = create_rotating_handler(LLM_LOG)
    all_handler = create_rotating_handler(ALL_LOG)

    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=console_renderer,
        foreign_pre_chain=shared_processors,
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(log_level)

    _configure_logger(
        "vasily.core", core_handler, all_handler, console_handler, level=log_level
    )
    _configure_logger(
        "vasily.interaction",
        interaction_handler,
        all_handler,
        console_handler,
        level=log_level,
    )
    _configure_logger(
        "vasily.plugins", plugins_handler, all_handler, console_handler, level=log_level
    )
    _configure_logger(
        "vasily.llm", llm_handler, all_handler, console_handler, level=log_level
    )

    _logging_initialized = True


def _configure_logger(name: str, *handlers, level: int) -> None:
    """Настроить или обновить существующий логгер."""
    logger = logging.getLogger(name)
    logger.handlers.clear()
    for handler in handlers:
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def get_logger(category: str, name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Получить логгер для категории. Возвращает LazyLogger (прокси),
    который создаёт реальный логгер только при первом вызове.
    """
    key = f"{category}:{name}" if name else category
    if key not in _logger_proxies:
        _logger_proxies[key] = LazyLogger(category, name)
    return _logger_proxies[key]  # type: ignore
