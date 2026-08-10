"""Memory Manager with Hot/Cold tiers and automatic compression."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.logging_config import get_logger
from memory.long_term import LongTermMemory

logger = get_logger("core", "MemoryManager")

HOT_FILE = "data/hot_memory.json"
COLD_FILE = "data/cold_memory.json"
HOT_RETENTION_HOURS = 72
COLD_RETENTION_DAYS = 27


class MemoryManager:
    """Two-tier memory: hot (72h) + cold (27d) with compression and locking."""

    def __init__(self):
        self.hot = LongTermMemory(HOT_FILE)
        self.cold_file = Path(COLD_FILE)
        self.cold_file.parent.mkdir(parents=True, exist_ok=True)
        self._cold_data = self._load_cold()

        # Lock for concurrent access protection
        self._lock = asyncio.Lock()

    def _load_cold(self) -> dict[str, Any]:
        if self.cold_file.exists():
            try:
                with open(self.cold_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cold(self) -> None:
        try:
            with open(self.cold_file, "w", encoding="utf-8") as f:
                json.dump(self._cold_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save cold memory", error=str(e))

    async def remember(self, key: str, value: Any) -> None:
        """Store in hot memory with locking."""
        async with self._lock:
            self.hot.remember(key, value)

    async def recall(self, key: str, default: Any = None) -> Any:
        """Retrieve from hot (if not expired) or cold with locking."""
        async with self._lock:
            return self._recall_unlocked(key, default)

    def _recall_unlocked(self, key: str, default: Any = None) -> Any:
        """Lock-free recall. Caller must already hold self._lock."""
        entry = self.hot.get_all_entries().get(key)
        if entry:
            created = datetime.fromisoformat(entry["created_at"])
            age = datetime.now() - created
            if age.total_seconds() < HOT_RETENTION_HOURS * 3600:
                return entry.get("value")
            logger.debug("Hot entry expired", key=key)

        cold_entry = self._cold_data.get(key)
        if cold_entry:
            return cold_entry.get("summary")
        return default

    async def forget(self, key: str) -> bool:
        """Remove from both tiers with locking."""
        async with self._lock:
            removed = self.hot.forget(key)
            if key in self._cold_data:
                del self._cold_data[key]
                self._save_cold()
                removed = True
            return removed

    async def clean_expired_hot(self) -> int:
        """Remove entries older than HOT_RETENTION_HOURS with locking."""
        async with self._lock:
            cutoff = datetime.now() - timedelta(hours=HOT_RETENTION_HOURS)
            to_delete = [
                key
                for key, entry in self.hot.get_all_entries().items()
                if datetime.fromisoformat(entry["created_at"]) < cutoff
            ]
            for key in to_delete:
                self.hot.forget(key)
            if to_delete:
                logger.info("Cleaned expired hot memory", removed=len(to_delete))
            return len(to_delete)

    async def compress_to_cold(self, key: str, compressor: Callable[[Any], Awaitable[str]]) -> bool:
        """Compress one entry from hot to cold with locking."""
        async with self._lock:
            entry = self.hot.get_all_entries().get(key)
            if not entry:
                return False

            try:
                summary = await compressor(entry.get("value"))
            except Exception as e:
                logger.error("Compression failed", key=key, error=str(e))
                return False

            self._cold_data[key] = {
                "summary": summary,
                "compressed_at": datetime.now().isoformat(),
                "original_created": entry["created_at"],
            }
            self._save_cold()
            self.hot.forget(key)
            logger.info("Compressed to cold", key=key)
            return True

    async def compress_all_expired(self, compressor: Callable[[Any], Awaitable[str]]) -> int:
        """Compress all expired hot entries to cold."""
        cutoff = datetime.now() - timedelta(hours=HOT_RETENTION_HOURS)
        expired_keys = [
            key
            for key, entry in self.hot.get_all_entries().items()
            if datetime.fromisoformat(entry["created_at"]) < cutoff
        ]

        count = 0
        for key in expired_keys:
            if await self.compress_to_cold(key, compressor):
                count += 1
        return count

    async def compress_cycle(self, compressor) -> int:
        """Run one compression cycle: compress all expired hot entries.

        Designed to be registered as a periodic task in PeriodicScheduler
        (ADR-005). Does not manage its own loop - the scheduler does.
        """
        return await self.compress_all_expired(compressor)

    async def build_context(self, query: str, max_tokens: int = 3000) -> str:
        """Build RAG context: latest 5 hot + relevant cold entries."""
        async with self._lock:
            parts = []

            hot_keys = list(self.hot.get_all_entries().keys())[-5:]
            for key in hot_keys:
                value = self._recall_unlocked(key)
                if value is not None:
                    parts.append(f"[HOT] {key}: {str(value)[:200]}")

            query_words = set(query.lower().split())
            cold_matches = [
                entry.get("summary", "")
                for entry in self._cold_data.values()
                if any(word in entry.get("summary", "").lower() for word in query_words)
            ]
            if cold_matches:
                parts.append("[COLD] " + " ".join(cold_matches[:3]))

            context = "\n".join(parts)
            return context[:max_tokens]

    def __len__(self) -> int:
        return len(self.hot) + len(self._cold_data)

    def __contains__(self, key: str) -> bool:
        return key in self.hot.get_all_entries() or key in self._cold_data
