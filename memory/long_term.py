"""Long-term memory module - JSON key-value storage."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

logger = get_logger("core", "LongTermMemory")

DEFAULT_MEMORY_FILE = "data/long_memory.json"


class LongTermMemory:
    """Persistent key-value memory storage."""

    def __init__(self, memory_file: str = DEFAULT_MEMORY_FILE):
        self.memory_file = Path(memory_file)
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load memory from file."""
        if self.memory_file.exists():
            try:
                with open(self.memory_file, encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.info("Memory loaded", entries_count=len(self._data))
            except (OSError, json.JSONDecodeError) as e:
                logger.error("Failed to load memory", error=str(e))
                self._data = {}
        else:
            logger.info("Memory file not found, starting fresh")
            self._data = {}

    def _save(self) -> None:
        """Save memory to file."""
        try:
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error("Failed to save memory", error=str(e))

    def remember(self, key: str, value: Any) -> None:
        """Store a value in memory."""
        self._data[key] = {
            "value": value,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        self._save()
        logger.info("Memory stored", key=key)

    def recall(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from memory."""
        entry = self._data.get(key)
        if entry is None:
            return default
        return entry.get("value", default)

    def forget(self, key: str) -> bool:
        """Remove a value from memory."""
        if key in self._data:
            del self._data[key]
            self._save()
            logger.info("Memory forgotten", key=key)
            return True
        return False

    def list_keys(self) -> list:
        """List all memory keys."""
        return list(self._data.keys())

    def clear(self) -> None:
        """Clear all memory."""
        self._data = {}
        self._save()
        logger.info("Memory cleared")

    def __len__(self) -> int:
        return len(self._data)

    def get_all_entries(self) -> dict[str, dict]:
        """Get all entries (for use by MemoryManager)."""
        return self._data.copy()
