"""Gradient Cascade Memory — новая архитектура памяти Vasily AI.
Три зоны:
- TGS (score > 50): абсолютная защита, сырые данные
- HOT (0.1..50): активная память, сырые данные
- COLD (0..-49.9): сжатые саммари
1 тик = 1 сообщение (user + assistant)
DECAY_ACTUAL = max(0.01, 0.1 - (COUNT_REQUESTS * 0.0003))
Нагрев:
- recall: +5.0
- remember: +10.0
Остывание:
- перезапуск: -2.0 ко всем (кроме TGS)
- каждый тик: -DECAY_ACTUAL
Компрессия: диапазон 5..-4
Protected:
- включается при recall из Cold
- снимается только при remember + score >= 8.0
- защищает от компрессии, НЕ от остывания
"""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

logger = get_logger("core", "GradientMemory")

# Константы
TGS_THRESHOLD = 50.0
HOT_MIN = 0.1
COLD_MIN = -49.9
DELETE_THRESHOLD = -50.0
REGULAR_HEAT = 5.0
REINFORCE_HEAT = 10.0
DECAY_PER_SESSION_CLOSE = 2.0
COMPRESSION_RANGE_LOW = 5.0
COMPRESSION_RANGE_HIGH = -4.0
PROTECTED_HEAT_REQUIRED = 8.0
DEFAULT_SIMPLE_SCORE = 25.0
DEFAULT_COMPLEX_SCORE = 40.0
LOCK_TIMEOUT = 2.0
TGS_FILE = "data/tgs_memory.json"
HOT_FILE = "data/tg_hot_memory.json"
COLD_FILE = "data/tg_cold_memory.json"
TEMP_SUFFIX = ".tmp"


class GradientMemory:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tgs_file = self.data_dir / "tgs_memory.json"
        self.hot_file = self.data_dir / "tg_hot_memory.json"
        self.cold_file = self.data_dir / "tg_cold_memory.json"
        self._read_lock = asyncio.Semaphore(5)
        self._write_lock = asyncio.Lock()
        self._session_requests = 0
        self._session_count = 0
        self._tgs: dict[str, dict] = {}
        self._hot: dict[str, dict] = {}
        self._cold: dict[str, dict] = {}
        self._load_all()

    # ---------- Lock helpers ----------
    async def _acquire_read(self):
        try:
            await asyncio.wait_for(self._read_lock.acquire(), timeout=LOCK_TIMEOUT)
        except TimeoutError:
            logger.error("Read lock acquisition timeout")
            raise

    async def _acquire_write(self):
        try:
            await asyncio.wait_for(self._write_lock.acquire(), timeout=LOCK_TIMEOUT)
        except TimeoutError:
            logger.error("Write lock acquisition timeout")
            raise

    def _release_read(self):
        self._read_lock.release()

    def _release_write(self):
        self._write_lock.release()

    # ---------- Load / Save ----------
    def _load_all(self) -> None:
        self._tgs = self._load_zone(self.tgs_file)
        self._hot = self._load_zone(self.hot_file)
        self._cold = self._load_zone(self.cold_file)

        # Удаляем дубликаты: если ключ есть в TGS, удаляем из HOT и COLD
        for key in list(self._hot.keys()):
            if key in self._tgs:
                del self._hot[key]
        for key in list(self._cold.keys()):
            if key in self._tgs:
                del self._cold[key]

        logger.info(
            "GradientMemory loaded", tgs=len(self._tgs), hot=len(self._hot), cold=len(self._cold)
        )

    def _load_zone(self, path: Path) -> dict[str, dict]:
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load memory zone", path=str(path), error=str(e))
            return {}

    async def _save_zone(self, zone: str, data: dict[str, dict]) -> None:
        path_map = {"tgs": self.tgs_file, "hot": self.hot_file, "cold": self.cold_file}
        path = path_map.get(zone)
        if not path:
            raise ValueError(f"Unknown zone: {zone}")
        temp_path = path.with_suffix(path.suffix + TEMP_SUFFIX)
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            os.replace(temp_path, path)
            logger.debug("Zone saved", zone=zone, count=len(data))
        except Exception as e:
            logger.error("Failed to save zone", zone=zone, error=str(e))
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            raise

    async def _save_all(self) -> None:
        await self._save_zone("tgs", self._tgs)
        await self._save_zone("hot", self._hot)
        await self._save_zone("cold", self._cold)

    # ---------- Основные операции ----------
    async def remember(self, key: str, value: Any, complex_query: bool = False) -> None:
        await self._acquire_write()
        try:
            initial_score = DEFAULT_COMPLEX_SCORE if complex_query else DEFAULT_SIMPLE_SCORE
            entry = {
                "value": value,
                "score": initial_score,
                "is_cold": False,
                "protected": False,
                "shield": False,
                "summary": None,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }

            existing = self._find_entry_unlocked(key)

            if existing:
                entry["score"] = existing.get("score", 0) + REINFORCE_HEAT
                entry["summary"] = existing.get("summary")
                entry["created_at"] = existing.get("created_at", datetime.now().isoformat())
                entry["is_cold"] = False

                if key in self._tgs:
                    self._tgs[key] = entry
                    await self._save_zone("tgs", self._tgs)
                    logger.info("Remember: updated in TGS", key=key, score=entry["score"])
                    return

                if key in self._hot:
                    self._hot[key] = entry
                    await self._save_zone("hot", self._hot)
                    logger.info("Remember: updated in HOT", key=key, score=entry["score"])
                    await self._check_promote_to_tgs_unlocked(key)
                    return

                if key in self._cold:
                    entry["protected"] = True
                    entry["summary"] = self._cold[key].get("summary")
                    del self._cold[key]
                    await self._save_zone("cold", self._cold)
                    self._hot[key] = entry
                    await self._save_zone("hot", self._hot)
                    logger.info("Remember: moved from COLD to HOT", key=key, score=entry["score"])
                    await self._check_promote_to_tgs_unlocked(key)
                    return

            # Новая запись
            self._hot[key] = entry
            await self._save_zone("hot", self._hot)
            logger.info("Remember: stored (new)", key=key, score=entry["score"])
            await self._check_promote_to_tgs_unlocked(key)

        finally:
            self._release_write()

    async def recall(self, key: str) -> Any | None:
        await self._acquire_read()
        try:
            entry = self._find_entry_unlocked(key)
            if not entry:
                return None
            if key in self._cold:
                entry = self._cold[key].copy()
                entry["score"] = 10.0
                entry["protected"] = True
                entry["is_cold"] = False
                entry["updated_at"] = datetime.now().isoformat()
                del self._cold[key]
                await self._save_zone("cold", self._cold)
                self._hot[key] = entry
                await self._save_zone("hot", self._hot)
                logger.info("Recall: moved from COLD to HOT", key=key, score=entry["score"])
                return entry.get("value")
            entry["score"] = min(entry.get("score", 0) + REGULAR_HEAT, 100.0)
            entry["updated_at"] = datetime.now().isoformat()
            zone = self._get_zone_unlocked(key)
            if zone:
                await self._save_zone(zone, getattr(self, f"_{zone}"))
                logger.info("Recall: heated", key=key, new_score=entry["score"])
                await self._check_promote_to_tgs_unlocked(key)
            return entry.get("value")
        finally:
            self._release_read()

    def _find_entry_unlocked(self, key: str) -> dict | None:
        if key in self._tgs:
            return self._tgs[key]
        if key in self._hot:
            return self._hot[key]
        if key in self._cold:
            return self._cold[key]
        return None

    def _get_zone_unlocked(self, key: str) -> str | None:
        if key in self._tgs:
            return "tgs"
        if key in self._hot:
            return "hot"
        if key in self._cold:
            return "cold"
        return None

    # ---------- Продвижение в TGS ----------
    async def _check_promote_to_tgs_unlocked(self, key: str) -> None:
        entry = self._find_entry_unlocked(key)
        if not entry:
            return
        score = entry.get("score", 0)
        if score > TGS_THRESHOLD:
            if key in self._tgs:
                return
            # Удаляем из HOT или COLD
            if key in self._hot:
                del self._hot[key]
            elif key in self._cold:
                del self._cold[key]
            else:
                return
            entry["shield"] = True
            self._tgs[key] = entry
            await self._save_zone("tgs", self._tgs)
            logger.info("Promoted to TGS", key=key, score=score)

    # ---------- Остывание ----------
    async def decay(self, count_requests: int) -> None:
        await self._acquire_write()
        try:
            decay_actual = max(0.01, 0.1 - (count_requests * 0.0003))
            changed = False
            for key, entry in list(self._hot.items()):
                if entry.get("protected", False):
                    continue
                new_score = entry.get("score", 0) - decay_actual
                entry["score"] = max(new_score, DELETE_THRESHOLD)
                entry["updated_at"] = datetime.now().isoformat()
                changed = True
                if entry["score"] <= DELETE_THRESHOLD:
                    del self._hot[key]
                    logger.info("Decay: deleted from HOT", key=key)
                    changed = True
            if changed:
                await self._save_zone("hot", self._hot)

            changed = False
            for key, entry in list(self._cold.items()):
                new_score = entry.get("score", 0) - (decay_actual * 0.5)
                entry["score"] = max(new_score, DELETE_THRESHOLD)
                entry["updated_at"] = datetime.now().isoformat()
                changed = True
                if entry["score"] <= DELETE_THRESHOLD:
                    del self._cold[key]
                    logger.info("Decay: deleted from COLD", key=key)
                    changed = True
            if changed:
                await self._save_zone("cold", self._cold)
        finally:
            self._release_write()

    async def session_close(self) -> None:
        await self._acquire_write()
        try:
            changed = False
            for key, entry in list(self._hot.items()):
                if entry.get("shield", False):
                    continue
                new_score = entry.get("score", 0) - DECAY_PER_SESSION_CLOSE
                entry["score"] = max(new_score, DELETE_THRESHOLD)
                entry["updated_at"] = datetime.now().isoformat()
                changed = True
                if entry["score"] <= DELETE_THRESHOLD:
                    del self._hot[key]
                    logger.info("Session close: deleted from HOT", key=key)
                    changed = True
            if changed:
                await self._save_zone("hot", self._hot)

            changed = False
            for key, entry in list(self._cold.items()):
                new_score = entry.get("score", 0) - DECAY_PER_SESSION_CLOSE
                entry["score"] = max(new_score, DELETE_THRESHOLD)
                entry["updated_at"] = datetime.now().isoformat()
                changed = True
                if entry["score"] <= DELETE_THRESHOLD:
                    del self._cold[key]
                    logger.info("Session close: deleted from COLD", key=key)
                    changed = True
            if changed:
                await self._save_zone("cold", self._cold)

            self._session_count += 1
            logger.info("Session close applied", session=self._session_count)
        finally:
            self._release_write()

    # ---------- Компрессия ----------
    async def compress_cycle(self, compressor: Callable[[Any], Awaitable[str]]) -> int:
        await self._acquire_write()
        try:
            compressed = 0
            for key, entry in list(self._hot.items()):
                score = entry.get("score", 0)
                if not (COMPRESSION_RANGE_HIGH <= score <= COMPRESSION_RANGE_LOW):
                    continue
                if entry.get("protected", False):
                    logger.debug("Compression skipped: protected", key=key)
                    continue

                existing_summary = entry.get("summary")
                if existing_summary and not existing_summary.startswith("Compressed:"):
                    cold_entry = {
                        "value": None,
                        "score": -5.0,
                        "is_cold": True,
                        "protected": False,
                        "shield": False,
                        "summary": existing_summary,
                        "created_at": entry.get("created_at", datetime.now().isoformat()),
                        "updated_at": datetime.now().isoformat(),
                    }
                else:
                    try:
                        summary = await compressor(entry.get("value", ""))
                        if summary and not summary.startswith("Compressed:"):
                            cold_entry = {
                                "value": None,
                                "score": -5.0,
                                "is_cold": True,
                                "protected": False,
                                "shield": False,
                                "summary": summary,
                                "created_at": entry.get("created_at", datetime.now().isoformat()),
                                "updated_at": datetime.now().isoformat(),
                            }
                        else:
                            value = entry.get("value", {})
                            if isinstance(value, dict):
                                user = value.get("user", "")
                                assistant = value.get("assistant", "")
                                summary = f"Пользователь спрашивал: {user[:150]}. Ответ ассистента: {assistant[:150]}."
                            else:
                                summary = str(value)[:300]
                            cold_entry = {
                                "value": None,
                                "score": -5.0,
                                "is_cold": True,
                                "protected": False,
                                "shield": False,
                                "summary": summary,
                                "created_at": entry.get("created_at", datetime.now().isoformat()),
                                "updated_at": datetime.now().isoformat(),
                            }
                    except Exception as e:
                        logger.error("Compression failed", key=key, error=str(e))
                        continue

                del self._hot[key]
                await self._save_zone("hot", self._hot)
                self._cold[key] = cold_entry
                await self._save_zone("cold", self._cold)
                compressed += 1
                logger.info(
                    "Compressed to COLD",
                    key=key,
                    summary_len=len(str(cold_entry.get("summary", ""))),
                )
            return compressed
        finally:
            self._release_write()

    # ---------- Команды забыть ----------
    async def forget(self, key: str) -> bool:
        await self._acquire_write()
        try:
            entry = self._find_entry_unlocked(key)
            if not entry:
                return False
            if key in self._tgs:
                entry["score"] = max(entry.get("score", 0) - 20.0, 0.0)
                entry["shield"] = False
                await self._save_zone("tgs", self._tgs)
                del self._tgs[key]
                self._hot[key] = entry
                await self._save_zone("hot", self._hot)
                logger.info("Forget: moved from TGS to HOT", key=key)
                return True
            zone = self._get_zone_unlocked(key)
            if not zone:
                return False
            entry["score"] = entry.get("score", 0) - 50.0
            entry["updated_at"] = datetime.now().isoformat()
            if entry["score"] <= DELETE_THRESHOLD:
                if zone == "hot":
                    del self._hot[key]
                    await self._save_zone("hot", self._hot)
                elif zone == "cold":
                    del self._cold[key]
                    await self._save_zone("cold", self._cold)
                logger.info("Forget: deleted", key=key)
            else:
                if zone == "hot":
                    entry["is_cold"] = True
                    del self._hot[key]
                    self._cold[key] = entry
                    await self._save_zone("hot", self._hot)
                    await self._save_zone("cold", self._cold)
                    logger.info("Forget: moved to COLD", key=key)
            return True
        finally:
            self._release_write()

    async def forget_all(self, confirm: bool = False) -> bool:
        """Забыть всё (ротация: TGS→HOT, HOT→COLD). После ротации COLD полностью очищается."""
        if not confirm:
            return False
        await self._acquire_write()
        try:
            # Шаг 0: Очищаем COLD (все записи удаляются)
            self._cold.clear()

            # Шаг 1: TGS → HOT (снимаем защиту, штраф -20)
            for key, entry in list(self._tgs.items()):
                entry["score"] = max(entry.get("score", 50.0) - 20.0, 0.0)
                entry["shield"] = False
                entry["updated_at"] = datetime.now().isoformat()
                del self._tgs[key]
                self._hot[key] = entry

            # Шаг 2: HOT → COLD (компрессия, сохраняем score)
            for key, entry in list(self._hot.items()):
                current_score = entry.get("score", 0.0)
                if current_score is None:
                    current_score = 0.0
                new_score = max(current_score - 10.0, DELETE_THRESHOLD)

                summary = entry.get("summary")
                if not summary or summary.startswith("Compressed:"):
                    value = entry.get("value", {})
                    if isinstance(value, dict):
                        user = value.get("user", "")
                        assistant = value.get("assistant", "")
                        summary = f"Пользователь спрашивал: {user[:150]}. Ответ ассистента: {assistant[:150]}."
                    else:
                        summary = str(value)[:200]

                cold_entry = {
                    "value": None,
                    "score": new_score,
                    "is_cold": True,
                    "protected": False,
                    "shield": False,
                    "summary": summary,
                    "created_at": entry.get("created_at", datetime.now().isoformat()),
                    "updated_at": datetime.now().isoformat(),
                }
                del self._hot[key]
                self._cold[key] = cold_entry

            # Шаг 3: Удаляем из COLD записи с score <= -50.0 (на всякий случай)
            deleted = 0
            for key, entry in list(self._cold.items()):
                if entry.get("score", -5.0) <= DELETE_THRESHOLD:
                    del self._cold[key]
                    deleted += 1

            await self._save_all()
            logger.info(
                "Forget all: rotation completed",
                cold_entries=len(self._cold),
                deleted=deleted,
            )
            return True
        finally:
            self._release_write()

    # ---------- Build context ----------
    async def build_context(self, query: str, max_tokens: int = 3000) -> str:
        await self._acquire_read()
        try:
            parts = []
            tgs_items = sorted(self._tgs.items(), key=lambda x: x[1].get("score", 0), reverse=True)[
                :5
            ]
            for key, entry in tgs_items:
                parts.append(f"[TGS: {key}] {self._format_value(entry)}")

            hot_items = sorted(
                self._hot.items(),
                key=lambda x: (x[1].get("protected", False), x[1].get("score", 0)),
                reverse=True,
            )[:10]
            for key, entry in hot_items:
                parts.append(f"[HOT: {key}] {self._format_value(entry)}")

            query_words = set(query.lower().split())
            cold_items = []
            for key, entry in self._cold.items():
                summary = entry.get("summary", "")
                if any(word in summary.lower() for word in query_words):
                    cold_items.append((key, entry))
            cold_items = sorted(cold_items, key=lambda x: x[1].get("score", 0), reverse=True)[:3]
            for key, entry in cold_items:
                parts.append(f"[COLD: {key}] {entry.get('summary', '')}")

            context = "\n".join(parts)
            return context[:max_tokens]
        finally:
            self._release_read()

    @staticmethod
    def _format_value(entry: dict) -> str:
        value = entry.get("value")
        if value is None:
            return entry.get("summary", "")
        if isinstance(value, str):
            return value[:500]
        if isinstance(value, dict) or isinstance(value, list):
            return str(value)[:500]
        return str(value)[:500]

    # ---------- Вспомогательные ----------
    def __len__(self) -> int:
        return len(self._tgs) + len(self._hot) + len(self._cold)

    def get_stats(self) -> dict[str, Any]:
        return {
            "tgs": len(self._tgs),
            "hot": len(self._hot),
            "cold": len(self._cold),
            "total": len(self),
            "session_count": self._session_count,
            "session_requests": self._session_requests,
        }
