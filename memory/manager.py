"""Gradient Cascade Memory — новая архитектура памяти Vasily AI.
Stage 2 fixes (stability):
- recall() now uses the WRITE lock (it mutates zones and saves to disk;
  running it under the read semaphore was a race condition);
- compress_cycle() is three-phase: LLM calls happen WITHOUT holding the
  write lock, so remember/recall keep working during compression;
- stale "compressing" flags are stripped on load (crash self-healing);
- _check_promote_to_tgs_unlocked() also saves the source zone (no more
  duplicated entries between files until the next save);
- recall_memory() takes the read lock and tokenizes the query with
  re.findall (punctuation no longer breaks the search); same for
  build_context();
- remember() reinforcement is capped at 100.0 (was unbounded);
- decay() increments _session_requests (it is the per-request tick);
- dead constants removed; semantics of protected/shield documented.
"""

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

logger = get_logger("core", "GradientMemory")

TGS_THRESHOLD = 50.0
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
SCORE_CEILING = 100.0
LOCK_TIMEOUT = 2.0
TEMP_SUFFIX = ".tmp"
# Default zone file paths. Kept module-level so tests can monkeypatch
# them; see _zone_path() for resolution rules.
TGS_FILE = "data/tgs_memory.json"
HOT_FILE = "data/tg_hot_memory.json"
COLD_FILE = "data/tg_cold_memory.json"

# Semantics of entry flags (stage 2: documented, behavior unchanged —
# ADR-012 will redesign the cooling model):
#   protected  — immune to periodic decay() (e.g. resurrected from COLD)
#   shield     — immune to session_close() decay (e.g. TGS entries)
#   compressing— transient marker set by compress_cycle phase 1; stripped
#                on load, so a crash mid-compression cannot wedge an entry


class GradientMemory:
    """Градиентно-сессионная память с динамическим охлаждением.

    Locking model:
    - Write operations (remember/forget/decay/session_close/compress_cycle/
      recall-with-promotion) use the single write lock and are serialized.
    - Read-only operations (recall_memory/build_context) take the read
      semaphore. They contain NO awaits inside — in a single-threaded event
      loop that makes them atomic. DO NOT add awaits to these methods
      without revisiting the locking model.
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tgs_file = self._zone_path(TGS_FILE, "tgs_memory.json")
        self.hot_file = self._zone_path(HOT_FILE, "tg_hot_memory.json")
        self.cold_file = self._zone_path(COLD_FILE, "tg_cold_memory.json")
        self._read_lock = asyncio.Semaphore(5)
        self._write_lock = asyncio.Lock()
        self._session_requests = 0
        self._session_count = 0
        self._tgs: dict[str, dict] = {}
        self._hot: dict[str, dict] = {}
        self._cold: dict[str, dict] = {}
        self._load_all()

    def _zone_path(self, configured: str, fallback_filename: str) -> Path:
        """Resolve a zone file path.

        An ABSOLUTE configured path (as produced by test monkeypatching)
        is used as-is; a relative default is resolved against data_dir,
        so a custom VASILY_DATA_DIR keeps working in production.
        """
        p = Path(configured)
        if p.is_absolute():
            return p
        return self.data_dir / fallback_filename

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

    def _load_all(self) -> None:
        self._tgs = self._load_zone(self.tgs_file)
        self._hot = self._load_zone(self.hot_file)
        self._cold = self._load_zone(self.cold_file)
        for key in list(self._hot.keys()):
            if key in self._tgs:
                del self._hot[key]
        for key in list(self._cold.keys()):
            if key in self._tgs:
                del self._cold[key]
        # Self-healing: strip stale transient markers left by a crash
        # in the middle of compress_cycle (otherwise such entries would
        # be skipped by every future compression attempt).
        for zone in (self._hot, self._cold, self._tgs):
            for entry in zone.values():
                entry.pop("compressing", None)
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
                entry["score"] = min(existing.get("score", 0) + REINFORCE_HEAT, SCORE_CEILING)
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
            self._hot[key] = entry
            await self._save_zone("hot", self._hot)
            logger.info("Remember: stored (new)", key=key, score=entry["score"])
            await self._check_promote_to_tgs_unlocked(key)
        finally:
            self._release_write()

    async def recall(self, key: str) -> Any | None:
        """Recall a fact by exact key. Heats the entry; resurrects from COLD.

        Uses the WRITE lock: resurrection mutates zones and saves to disk,
        which is unsafe under the shared read semaphore (stage 2 fix).
        """
        await self._acquire_write()
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
            entry["score"] = min(entry.get("score", 0) + REGULAR_HEAT, SCORE_CEILING)
            entry["updated_at"] = datetime.now().isoformat()
            zone = self._get_zone_unlocked(key)
            if zone:
                await self._save_zone(zone, getattr(self, f"_{zone}"))
                logger.info("Recall: heated", key=key, new_score=entry["score"])
                await self._check_promote_to_tgs_unlocked(key)
            return entry.get("value")
        finally:
            self._release_write()

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

    async def _check_promote_to_tgs_unlocked(self, key: str) -> None:
        """Promote a hot entry to TGS when its score crosses the threshold.

        Must be called under the WRITE lock. Saves BOTH the TGS zone and
        the source zone: previously the source was left stale on disk,
        so the entry temporarily existed in two files (loader deduplicated
        it, but the files disagreed until the next source-zone save).
        """
        entry = self._find_entry_unlocked(key)
        if not entry:
            return
        score = entry.get("score", 0)
        if score > TGS_THRESHOLD:
            if key in self._tgs:
                return
            if key in self._hot:
                del self._hot[key]
                source_zone = "hot"
            elif key in self._cold:
                del self._cold[key]
                source_zone = "cold"
            else:
                return
            entry["shield"] = True
            self._tgs[key] = entry
            await self._save_zone("tgs", self._tgs)
            await self._save_zone(source_zone, getattr(self, f"_{source_zone}"))
            logger.info("Promoted to TGS", key=key, score=score)

    async def decay(self, count_requests: int) -> None:
        """Periodic cooling. Called once per user request by AgentCore
        (count_requests = session request count, used to slow down decay
        in long sessions). Also ticks the session request counter.
        """
        await self._acquire_write()
        try:
            self._session_requests = min(self._session_requests + 1, 1000)
            decay_actual = max(0.01, 0.1 - (count_requests * 0.0003))
            hot_changed = False
            for key, entry in list(self._hot.items()):
                if entry.get("protected", False):
                    continue
                new_score = entry.get("score", 0) - decay_actual
                entry["score"] = max(new_score, DELETE_THRESHOLD)
                entry["updated_at"] = datetime.now().isoformat()
                hot_changed = True
                if entry["score"] <= DELETE_THRESHOLD:
                    del self._hot[key]
                    logger.info("Decay: deleted from HOT", key=key)
            if hot_changed:
                await self._save_zone("hot", self._hot)
            cold_changed = False
            for key, entry in list(self._cold.items()):
                new_score = entry.get("score", 0) - (decay_actual * 0.5)
                entry["score"] = max(new_score, DELETE_THRESHOLD)
                entry["updated_at"] = datetime.now().isoformat()
                cold_changed = True
                if entry["score"] <= DELETE_THRESHOLD:
                    del self._cold[key]
                    logger.info("Decay: deleted from COLD", key=key)
            if cold_changed:
                await self._save_zone("cold", self._cold)
        finally:
            self._release_write()

    async def session_close(self) -> None:
        """End-of-session cooling: all non-shielded entries cool down a bit."""
        await self._acquire_write()
        try:
            hot_changed = False
            for key, entry in list(self._hot.items()):
                if entry.get("shield", False):
                    continue
                new_score = entry.get("score", 0) - DECAY_PER_SESSION_CLOSE
                entry["score"] = max(new_score, DELETE_THRESHOLD)
                entry["updated_at"] = datetime.now().isoformat()
                hot_changed = True
                if entry["score"] <= DELETE_THRESHOLD:
                    del self._hot[key]
                    logger.info("Session close: deleted from HOT", key=key)
            if hot_changed:
                await self._save_zone("hot", self._hot)
            cold_changed = False
            for key, entry in list(self._cold.items()):
                new_score = entry.get("score", 0) - DECAY_PER_SESSION_CLOSE
                entry["score"] = max(new_score, DELETE_THRESHOLD)
                entry["updated_at"] = datetime.now().isoformat()
                cold_changed = True
                if entry["score"] <= DELETE_THRESHOLD:
                    del self._cold[key]
                    logger.info("Session close: deleted from COLD", key=key)
            if cold_changed:
                await self._save_zone("cold", self._cold)
            self._session_count += 1
            logger.info("Session close applied", session=self._session_count)
        finally:
            self._release_write()

    async def compress_cycle(self, compressor: Callable[[Any], Awaitable[str]]) -> int:
        """Compress cooled HOT entries into COLD.

        Three-phase design (stage 2 fix): the LLM call happens WITHOUT
        holding the write lock, so remember/recall keep working while
        compression runs. Phase 1 marks candidates with a transient
        "compressing" flag; phase 3 re-checks each candidate (it may have
        been heated, forgotten or replaced during the LLM call).
        """
        # Phase 1: pick and mark candidates (fast, under write lock)
        await self._acquire_write()
        try:
            candidates: list[tuple[str, dict]] = []
            for key, entry in list(self._hot.items()):
                score = entry.get("score", 0)
                if not (COMPRESSION_RANGE_HIGH <= score <= COMPRESSION_RANGE_LOW):
                    continue
                if entry.get("protected", False) or entry.get("compressing", False):
                    continue
                entry["compressing"] = True
                candidates.append((key, dict(entry)))
            if candidates:
                await self._save_zone("hot", self._hot)
        finally:
            self._release_write()

        if not candidates:
            return 0

        # Phase 2: summarize candidates (slow, NO lock held)
        summaries: dict[str, str | None] = {}
        for key, entry in candidates:
            existing_summary = entry.get("summary")
            if existing_summary and not existing_summary.startswith("Compressed:"):
                summaries[key] = existing_summary
                continue
            try:
                value = entry.get("value")
                summary_from_llm = await compressor(value)
                if summary_from_llm and not summary_from_llm.startswith("Compressed:"):
                    summaries[key] = summary_from_llm
                else:
                    summaries[key] = self._extract_fallback_summary(value)
            except Exception as e:
                logger.error("Compression failed", key=key, error=str(e))
                summaries[key] = None

        # Phase 3: apply results (under write lock), re-checking each entry
        await self._acquire_write()
        try:
            compressed = 0
            dirty_hot = False
            dirty_cold = False
            for key, summary in summaries.items():
                live = self._hot.get(key)
                # The entry must still be the one we marked in phase 1:
                # if the flag is gone, it was replaced by a new remember().
                if live is None or not live.get("compressing", False):
                    continue
                live.pop("compressing", None)
                score = live.get("score", 0)
                # The entry may have been heated during the LLM call —
                # in that case it deserves to stay in HOT.
                if not (COMPRESSION_RANGE_HIGH <= score <= COMPRESSION_RANGE_LOW) or live.get(
                    "protected", False
                ):
                    dirty_hot = True
                    continue
                if summary is None:
                    dirty_hot = True
                    continue
                cold_entry = {
                    "value": None,
                    "score": -5.0,
                    "is_cold": True,
                    "protected": False,
                    "shield": False,
                    "summary": summary or "Факты из диалога не извлечены.",
                    "created_at": live.get("created_at", datetime.now().isoformat()),
                    "updated_at": datetime.now().isoformat(),
                }
                del self._hot[key]
                self._cold[key] = cold_entry
                dirty_hot = True
                dirty_cold = True
                compressed += 1
                logger.info(
                    "Compressed to COLD",
                    key=key,
                    summary_len=len(str(cold_entry.get("summary", ""))),
                )
            if dirty_hot:
                await self._save_zone("hot", self._hot)
            if dirty_cold:
                await self._save_zone("cold", self._cold)
            return compressed
        finally:
            self._release_write()

    @staticmethod
    def _extract_fallback_summary(value: Any) -> str:
        """Extract a summary from value without LLM (fallback)."""
        if value is None:
            return ""
        if isinstance(value, dict):
            if "summary" in value:
                return value["summary"]
            if "user" in value and "assistant" in value:
                user = value.get("user", "")
                assistant = value.get("assistant", "")
                return (
                    f"Пользователь спрашивал: {user[:150]}. "
                    f"Ответ ассистента: {assistant[:150]}."
                )
        return str(value)[:300]

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
        """Full memory rotation.

        TGS -> HOT (score -20, shield off)
        HOT -> COLD (score -50, is_cold, fast non-LLM summary)
        COLD -> DELETE (score -50, delete when score <= threshold;
                applied only to entries that were in COLD BEFORE the call)
        Note: HOT -> COLD here intentionally uses fast truncated summaries,
        NOT the LLM compressor — rotation must stay quick even on huge
        buffers. LLM-based compression happens in compress_cycle.
        user_fact: entries are exempt from rotation.
        """
        if not confirm:
            return False
        await self._acquire_write()
        try:
            original_cold_keys = set(self._cold.keys())

            # 1. TGS -> HOT
            for key, entry in list(self._tgs.items()):
                # Защита пользовательских фактов
                if key.startswith("user_fact:"):
                    logger.debug("Forget all: skipping user_fact", key=key)
                    continue
                current_score = entry.get("score", 50.0)
                new_score = max(current_score - 20.0, 0.0)
                entry["score"] = new_score
                entry["shield"] = False
                entry["updated_at"] = datetime.now().isoformat()
                del self._tgs[key]
                self._hot[key] = entry
                logger.info(
                    "Forget all: TGS -> HOT",
                    key=key,
                    old_score=current_score,
                    new_score=new_score,
                )

            # 2. HOT -> COLD
            for key, entry in list(self._hot.items()):
                # Защита пользовательских фактов
                if key.startswith("user_fact:"):
                    logger.debug("Forget all: skipping user_fact", key=key)
                    continue
                current_score = entry.get("score", 25.0)
                new_score = current_score - 50.0
                entry["score"] = new_score
                entry["is_cold"] = True
                entry["updated_at"] = datetime.now().isoformat()
                summary = self._extract_fallback_summary(entry.get("value"))
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
                logger.info(
                    "Forget all: HOT -> COLD",
                    key=key,
                    old_score=current_score,
                    new_score=new_score,
                )

            # 3. COLD -> DELETE (только для записей, бывших в COLD до вызова)
            for key in original_cold_keys:
                if key not in self._cold:
                    continue
                # Защита пользовательских фактов
                if key.startswith("user_fact:"):
                    logger.debug("Forget all: skipping user_fact in COLD", key=key)
                    continue
                entry = self._cold[key]
                current_score = entry.get("score", -5.0)
                new_score = current_score - 50.0
                entry["score"] = new_score
                entry["updated_at"] = datetime.now().isoformat()
                if new_score <= DELETE_THRESHOLD:
                    del self._cold[key]
                    logger.info("Forget all: COLD -> DELETE", key=key, score=new_score)
                else:
                    self._cold[key] = entry
                    logger.info(
                        "Forget all: COLD updated",
                        key=key,
                        old_score=current_score,
                        new_score=new_score,
                    )

            await self._save_all()
            logger.info("Forget all: rotation completed")
            return True
        finally:
            self._release_write()

    def has_compression_candidates(self) -> bool:
        """Cheap sync scan: are there HOT entries ready for HOT→COLD compression?

        Sync and lock-free on purpose: it contains no awaits, so it is atomic
        inside the event loop (see locking model in the class docstring).
        Used by AgentCore's lazy compression trigger.
        """
        return any(
            COMPRESSION_RANGE_HIGH <= entry.get("score", 0) <= COMPRESSION_RANGE_LOW
            and not entry.get("protected", False)
            and not entry.get("compressing", False)
            for entry in self._hot.values()
        )

    async def recall_memory(self, query: str) -> dict:
        """Search for facts in HOT and COLD zones by keywords.
        TGS is excluded to avoid duplication with system prompt.
        Returns structured result: {"found": bool, "facts": list[dict]}
        ADR-011: Lazy Retrieval tool backend.

        Stage 2: takes the read lock; the query is tokenized with
        re.findall(r"\\w+"), so punctuation no longer breaks matching
        ("пользователя?" now finds "пользователя").
        """
        if not query:
            return {"found": False, "facts": []}

        await self._acquire_read()
        try:
            query_words = set(re.findall(r"\w+", query.lower()))
            results = []

            # Search in HOT
            for key, entry in self._hot.items():
                value = entry.get("value")
                summary = entry.get("summary", "")
                text_to_search = ""

                # Собираем текст из всех возможных источников
                if isinstance(value, str):
                    text_to_search = value.lower()
                elif isinstance(value, dict):
                    if "summary" in value:
                        text_to_search = value["summary"].lower()
                    elif "user" in value and "assistant" in value:
                        text_to_search = (
                            value.get("user", "") + " " + value.get("assistant", "")
                        ).lower()
                    else:
                        text_to_search = json.dumps(value, ensure_ascii=False).lower()
                elif isinstance(value, list):
                    text_to_search = json.dumps(value, ensure_ascii=False).lower()
                elif value is not None:
                    text_to_search = str(value).lower()

                # Добавляем summary (если есть)
                if summary:
                    text_to_search += " " + summary.lower()

                if any(word in text_to_search for word in query_words):
                    results.append(
                        {
                            "key": key,
                            "zone": "hot",
                            "score": entry.get("score", 0),
                            "value": value,
                            "summary": summary,
                        }
                    )

            # Search in COLD
            for key, entry in self._cold.items():
                summary = entry.get("summary", "")
                if any(word in summary.lower() for word in query_words):
                    results.append(
                        {
                            "key": key,
                            "zone": "cold",
                            "score": entry.get("score", 0),
                            "summary": summary,
                        }
                    )

            # Sort by score descending and take top 5
            results.sort(key=lambda x: x["score"], reverse=True)
            top_results = results[:5]

            return {
                "found": len(top_results) > 0,
                "facts": top_results,
            }
        finally:
            self._release_read()

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
            query_words = set(re.findall(r"\w+", query.lower()))
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
