"""Gradient Cascade Memory — Vasily AI (ADR-013 / ADR-014).

Зоны: TGS 50..60 (рабочий кэш топ-10) / HOT 0.1..49.9 (вход и фильтр) /
COLD -0.1..-49.9 (прихожая перед удалением). Бессмертной памяти нет.
Спека: docs/adr/013-gradient-cascade-memory.md, ADR-014 (physical forget).
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

# ============ ADR-013 §2: пулы зон ============
TGS_MIN = 50.0
TGS_MAX = 60.0  # §7: потолок нагрева внутри TGS
HOT_MIN = 0.1  # §2: нижняя граница HOT
HOT_PROMO = 49.9  # §4: выше — триггер промоции в TGS
COLD_MIN = -49.9  # §2: дно COLD
DELETE_BELOW = -50.0  # §2: удаление при score ниже этой точки
SCORE_CEILING = 60.0  # §4: глобальный потолок нагрева

# ============ ADR-013 §3: стартовое охлаждение ============
COLD_START_PENALTY = -2.0  # §3: всем, кроме TGS, при старте агента

# ============ ADR-013 §4: нагрев и стартовые скоры ============
HEAT_RECALL = 5.0  # §4: recall / heat_facts / использование TGS
HEAT_REMEMBER = 10.0  # §4: remember по существующему ключу
DEFAULT_SIMPLE_SCORE = 15.0  # системные записи (диалоговые саммари)
DEFAULT_COMPLEX_SCORE = 40.0  # комплексные записи
TGS_LIMIT = 10  # §7: лимит рабочей зоны
TGS_EVICT_SCORE = 40.0  # §7: score вытесненного из TGS в HOT

# ============ ADR-013 §4.1: остывание (decay за тик) ============
DECAY_HOT_BASE = -0.3
DECAY_HOT_ACTIVE = -0.7
DECAY_COLD_BASE = -0.5
DECAY_COLD_ACTIVE = -1.5
DECAY_TGS = -0.1  # §7: оба режима
ACTIVE_MODE_WINDOW = 10  # §4.1: тиков без recall_memory -> активный режим

# ============ ADR-013 §5: два пути вниз ============
DISTILL_TRIGGER = 5.0  # §5: score <= 5.0 (unprotected) -> очередь
MIGRATION_TRAP = 0.5  # §5: score <= 0.5 (no_compress) -> ловушка
MIGRATION_FEE = -0.7  # §5: миграционный штраф (НЕ холодная ставка)
DISTILLED_SCORE = -5.0  # §5: вход дистиллированной записи в COLD
REVIVAL_FLOOR = 2.0  # §6: max(текущий + N, 2.0)
REVIVAL_UNPROTECT_HEAT = 8.0  # §6: снятие no_compress: score >= 8 И изменён

# ============ ADR-013 §8: user_fact ============
FACT_IMMUNE_TICKS = 10  # §8: страховка от forget_all

# ============ сервисное ============
LOCK_TIMEOUT = 2.0
TEMP_SUFFIX = ".tmp"
META_FILE = "meta.json"  # §3.1: data/meta.json -> {"total_ticks": N}

TGS_FILE = "data/tgs_memory.json"
HOT_FILE = "data/tg_hot_memory.json"
COLD_FILE = "data/tg_cold_memory.json"


class GradientMemory:
    """Градиентно-каскадная память. Спека: ADR-013/014."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.tgs_file = self._zone_path(TGS_FILE, "tgs_memory.json")
        self.hot_file = self._zone_path(HOT_FILE, "tg_hot_memory.json")
        self.cold_file = self._zone_path(COLD_FILE, "tg_cold_memory.json")
        self.meta_file = self.data_dir / META_FILE

        self._read_lock = asyncio.Semaphore(5)
        self._write_lock = asyncio.Lock()

        self._tgs: dict[str, dict] = {}
        self._hot: dict[str, dict] = {}
        self._cold: dict[str, dict] = {}

        self._distill_queue: list[str] = []
        self._ticks_since_recall = 0
        self._total_ticks = 0

        self._load_all()
        self._load_meta()

    def _zone_path(self, configured: str, fallback_filename: str) -> Path:
        p = Path(configured)
        if p.is_absolute():
            return p
        return self.data_dir / fallback_filename

    # ---------------- meta.json ----------------

    def _load_meta(self) -> None:
        if not self.meta_file.exists():
            self._total_ticks = 0
            return
        try:
            with open(self.meta_file, encoding="utf-8") as f:
                data = json.load(f)
            self._total_ticks = int(data.get("total_ticks", 0))
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning("Failed to load meta, ticks reset", error=str(e))
            self._total_ticks = 0

    async def _save_meta(self) -> None:
        temp = self.meta_file.with_suffix(".tmp")
        try:
            with open(temp, "w", encoding="utf-8") as f:
                json.dump({"total_ticks": self._total_ticks}, f)
            os.replace(temp, self.meta_file)
        except Exception as e:
            logger.error("Failed to save meta", error=str(e))

    # ---------------- загрузка ----------------

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

        for zone in (self._tgs, self._hot, self._cold):
            for entry in zone.values():
                if entry.pop("protected", None) is not None:
                    entry["no_compress"] = True
                entry.pop("compressing", None)
                entry.setdefault("changed_since_revival", False)
                entry.setdefault("last_heat_tick", -1)
                entry.setdefault("created_tick", self._total_ticks)

        for entry in self._cold.values():
            if entry.get("value") is not None:
                entry["no_compress"] = True

        for entry in self._tgs.values():
            entry["score"] = min(max(entry.get("score", TGS_MIN), TGS_MIN), TGS_MAX)
        for entry in self._hot.values():
            entry["score"] = min(max(entry.get("score", HOT_MIN), HOT_MIN), HOT_PROMO)
        for entry in self._cold.values():
            entry["score"] = min(max(entry.get("score", DISTILLED_SCORE), COLD_MIN), -0.1)

        logger.info(
            "GradientMemory loaded",
            tgs=len(self._tgs),
            hot=len(self._hot),
            cold=len(self._cold),
            total_ticks=self._total_ticks,
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

    # ---------------- сохранение ----------------

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

    # ---------------- локи ----------------

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

    # ================= §4: нагрев =================

    def _apply_heat(self, entry: dict, amount: float, key: str) -> None:
        if entry.get("last_heat_tick") == self._total_ticks:
            entry["updated_at"] = datetime.now().isoformat()
            logger.debug("Heat suppressed (same tick)", key=key, amount=amount)
            return

        entry["last_heat_tick"] = self._total_ticks
        entry["updated_at"] = datetime.now().isoformat()

        new_score = entry.get("score", 0) + amount
        if new_score > SCORE_CEILING:
            new_score = SCORE_CEILING
        entry["score"] = new_score
        logger.debug("Heat applied", key=key, amount=amount, new_score=new_score)

    # ================= §6: revival =================

    def _revive_entry(self, entry: dict, heat_amount: float, key: str) -> None:
        entry["score"] = max(entry.get("score", 0) + heat_amount, REVIVAL_FLOOR)
        entry["no_compress"] = True
        entry["changed_since_revival"] = False
        entry["is_cold"] = False
        entry["shield"] = False
        entry["last_heat_tick"] = self._total_ticks
        entry["updated_at"] = datetime.now().isoformat()
        logger.info("Revived from COLD", key=key, score=entry["score"])

    def _try_unprotect(self, entry: dict, key: str) -> None:
        if (
            entry.get("no_compress")
            and entry.get("changed_since_revival")
            and entry.get("score", 0) >= REVIVAL_UNPROTECT_HEAT
        ):
            entry["no_compress"] = False
            logger.info("no_compress released", key=key, score=entry["score"])

    # ================= запись =================

    async def remember(self, key: str, value: Any, complex_query: bool = False) -> None:
        if key.startswith("user_fact:"):
            await self.remember_user_fact(key, value)
            return
        initial = DEFAULT_COMPLEX_SCORE if complex_query else DEFAULT_SIMPLE_SCORE
        await self._store_entry(key, value, initial)

    async def remember_user_fact(self, key: str, value: Any) -> None:
        await self._store_entry(key, value, 40.0)

    async def remember_dialogue_summary(self, key: str, value: Any) -> None:
        await self._store_entry(key, value, DEFAULT_SIMPLE_SCORE)

    async def _store_entry(self, key: str, value: Any, initial_score: float) -> None:
        await self._acquire_write()
        try:
            now = datetime.now().isoformat()
            entry = {
                "value": value,
                "score": initial_score,
                "is_cold": False,
                "no_compress": False,
                "shield": False,
                "summary": None,
                "changed_since_revival": False,
                "last_heat_tick": -1,
                "created_at": now,
                "updated_at": now,
                "created_tick": self._total_ticks,
            }

            existing = self._find_entry_unlocked(key)
            if existing:
                entry["score"] = existing.get("score", 0)
                entry["last_heat_tick"] = existing.get("last_heat_tick", -1)
                entry["created_at"] = existing.get("created_at", now)
                entry["created_tick"] = existing.get("created_tick", self._total_ticks)
                entry["summary"] = existing.get("summary")

                zone = self._get_zone_unlocked(key)

                if zone == "cold":
                    self._revive_entry(entry, HEAT_REMEMBER, key)
                    entry["changed_since_revival"] = True
                    entry["value"] = value
                    del self._cold[key]
                    await self._save_zone("cold", self._cold)
                    self._hot[key] = entry
                    await self._save_zone("hot", self._hot)
                    self._try_unprotect(entry, key)
                    logger.info("Remember: revived from COLD", key=key, score=entry["score"])
                    return

                self._apply_heat(entry, HEAT_REMEMBER, key)
                if existing.get("no_compress"):
                    entry["no_compress"] = True
                    entry["changed_since_revival"] = True
                    self._try_unprotect(entry, key)

                if zone == "tgs":
                    self._tgs[key] = entry
                    await self._save_zone("tgs", self._tgs)
                    logger.info("Remember: reinforced in TGS", key=key, score=entry["score"])
                    return

                self._hot[key] = entry
                await self._save_zone("hot", self._hot)
                await self._maybe_promote_to_tgs(key)
                return

            entry["last_heat_tick"] = self._total_ticks
            self._hot[key] = entry
            await self._save_zone("hot", self._hot)
            logger.info("Remember: stored (new)", key=key, score=entry["score"])
            await self._maybe_promote_to_tgs(key)
        finally:
            self._release_write()

    # ================= heat_facts =================

    async def heat_facts(self, keys: list[str]) -> None:
        await self._acquire_write()
        try:
            heated = 0
            touched: set[str] = set()
            for key in keys:
                entry = self._find_entry_unlocked(key)
                if not entry:
                    continue
                zone = self._get_zone_unlocked(key)
                if zone == "cold":
                    self._revive_entry(entry, HEAT_RECALL, key)
                    del self._cold[key]
                    self._hot[key] = entry
                    touched.update(("cold", "hot"))
                    heated += 1
                    continue
                self._apply_heat(entry, HEAT_RECALL, key)
                self._try_unprotect(entry, key)
                touched.add(zone)
                heated += 1
                if zone == "hot":
                    await self._maybe_promote_to_tgs(key)
            for z in ("tgs", "hot", "cold"):
                if z in touched:
                    await self._save_zone(z, getattr(self, f"_{z}"))
            if heated:
                logger.info("heat_facts applied", count=heated)
        finally:
            self._release_write()

    # ================= лестница промоции =================

    async def _maybe_promote_to_tgs(self, key: str) -> None:
        entry = self._hot.get(key)
        if not entry:
            return
        if entry.get("score", 0) <= HOT_PROMO:
            return

        entry["score"] = min(entry["score"], SCORE_CEILING)
        entry["shield"] = True
        del self._hot[key]
        self._tgs[key] = entry
        await self._save_zone("hot", self._hot)

        while len(self._tgs) > TGS_LIMIT:
            oldest_key = min(
                self._tgs.keys(),
                key=lambda k: self._tgs[k].get("updated_at", ""),
            )
            evicted = self._tgs.pop(oldest_key)
            evicted["score"] = TGS_EVICT_SCORE
            evicted["shield"] = False
            self._hot[oldest_key] = evicted
            logger.info("TGS evicted (LRU)", key=oldest_key, to="HOT(40)")

        await self._save_zone("tgs", self._tgs)
        logger.info("Promoted to TGS", key=key, score=entry["score"])

    # ================= стартовое охлаждение =================

    async def cold_start_penalty(self) -> None:
        await self._acquire_write()
        try:
            cold_before = set(self._cold.keys())
            hot_changed = False
            for key, entry in list(self._hot.items()):
                entry["score"] = round(entry.get("score", 0) + COLD_START_PENALTY, 1)
                entry["updated_at"] = datetime.now().isoformat()
                hot_changed = True
                if entry.get("no_compress", False):
                    if entry["score"] <= MIGRATION_TRAP:
                        await self._migrate_to_cold(key, entry)
                elif entry["score"] <= DISTILL_TRIGGER:
                    if key not in self._distill_queue:
                        self._distill_queue.append(key)
                        logger.info("Queued for distillation (cold start)", key=key)
            if hot_changed:
                await self._save_zone("hot", self._hot)

            cold_changed = False
            for key, entry in self._cold.items():
                if key not in cold_before:
                    continue
                entry["score"] = round(entry.get("score", 0) + COLD_START_PENALTY, 1)
                entry["updated_at"] = datetime.now().isoformat()
                cold_changed = True
            if cold_changed:
                await self._save_zone("cold", self._cold)

            logger.info("Cold start penalty applied", hot=len(self._hot), cold=len(self._cold))
        finally:
            self._release_write()

    async def _migrate_to_cold(self, key: str, entry: dict) -> None:
        entry["score"] = min(round(entry.get("score", 0) + MIGRATION_FEE, 1), -0.1)
        entry["is_cold"] = True
        del self._hot[key]
        self._cold[key] = entry
        await self._save_zone("hot", self._hot)
        await self._save_zone("cold", self._cold)
        logger.info("Migrated to COLD (as-is)", key=key, score=entry["score"])

    # ================= остывание =================

    async def decay(self, count_requests: int = 0) -> None:
        await self._acquire_write()
        try:
            self._total_ticks += 1
            self._ticks_since_recall += 1
            active = self._ticks_since_recall >= ACTIVE_MODE_WINDOW

            tgs_changed = False
            for entry in self._tgs.values():
                entry["score"] = round(entry.get("score", 0) + DECAY_TGS, 1)
                entry["updated_at"] = datetime.now().isoformat()
                tgs_changed = True
            await self._tgs_decay_demote()
            if tgs_changed:
                await self._save_zone("tgs", self._tgs)

            hot_rate = DECAY_HOT_ACTIVE if active else DECAY_HOT_BASE
            hot_changed = False
            migrated_this_tick: set[str] = set()
            for key, entry in list(self._hot.items()):
                new_score = round(entry.get("score", 0) + hot_rate, 1)
                if entry.get("no_compress", False):
                    if new_score <= MIGRATION_TRAP:
                        entry["score"] = new_score
                        await self._migrate_to_cold(key, entry)
                        migrated_this_tick.add(key)
                        hot_changed = True
                        continue
                else:
                    if new_score <= DISTILL_TRIGGER and key not in self._distill_queue:
                        self._distill_queue.append(key)
                        logger.info("Queued for distillation", key=key, score=new_score)
                if new_score <= DELETE_BELOW:
                    del self._hot[key]
                    logger.info("Decay: deleted from HOT", key=key)
                    hot_changed = True
                    continue
                entry["score"] = max(new_score, HOT_MIN)
                entry["updated_at"] = datetime.now().isoformat()
                hot_changed = True
            if hot_changed:
                await self._save_zone("hot", self._hot)

            cold_rate = DECAY_COLD_ACTIVE if active else DECAY_COLD_BASE
            cold_changed = False
            for key, entry in list(self._cold.items()):
                if key in migrated_this_tick:
                    continue
                new_score = round(entry.get("score", 0) + cold_rate, 1)
                cold_changed = True
                if new_score <= DELETE_BELOW:
                    del self._cold[key]
                    logger.info("Decay: deleted from COLD", key=key)
                else:
                    entry["score"] = new_score
                    entry["updated_at"] = datetime.now().isoformat()
            if cold_changed:
                await self._save_zone("cold", self._cold)

            await self._save_meta()
            logger.debug(
                "Decay tick done",
                tick=self._total_ticks,
                active=active,
                queued=len(self._distill_queue),
            )
        finally:
            self._release_write()

    async def _tgs_decay_demote(self) -> None:
        for key in list(self._tgs.keys()):
            if self._tgs[key].get("score", TGS_MIN) < TGS_MIN:
                entry = self._tgs.pop(key)
                entry["score"] = TGS_EVICT_SCORE
                entry["shield"] = False
                self._hot[key] = entry
                logger.info("TGS decay: demoted to HOT(40)", key=key)

    # ================= дистилляция =================

    def has_compression_candidates(self) -> bool:
        return len(self._distill_queue) > 0

    async def compress_cycle(self, compressor: Callable[[Any], Awaitable[str]]) -> int:
        await self._acquire_write()
        try:
            batch: list[tuple[str, dict]] = []
            while self._distill_queue and len(batch) < 5:
                key = self._distill_queue.pop(0)
                entry = self._hot.get(key)
                if entry is None or entry.get("compressing"):
                    continue
                if entry.get("score", 0) > DISTILL_TRIGGER or entry.get("no_compress"):
                    continue
                entry["compressing"] = True
                batch.append((key, dict(entry)))
            if batch:
                await self._save_zone("hot", self._hot)
        finally:
            self._release_write()

        if not batch:
            return 0

        summaries: dict[str, str | None] = {}
        for key, entry in batch:
            try:
                summary = await compressor(entry.get("value"))
                summaries[key] = summary if summary else None
            except Exception as e:
                logger.error("Distillation failed", key=key, error=str(e))
                summaries[key] = None

        await self._acquire_write()
        try:
            distilled = 0
            hot_dirty = False
            cold_dirty = False
            for key, summary in summaries.items():
                live = self._hot.get(key)
                if live is None or not live.get("compressing"):
                    continue
                live.pop("compressing", None)
                hot_dirty = True
                if live.get("score", 0) > DISTILL_TRIGGER or live.get("no_compress"):
                    continue
                if not summary:
                    continue
                cold_entry = {
                    "value": None,
                    "score": DISTILLED_SCORE,
                    "is_cold": True,
                    "no_compress": False,
                    "shield": False,
                    "summary": summary,
                    "changed_since_revival": False,
                    "last_heat_tick": -1,
                    "created_at": live.get("created_at", datetime.now().isoformat()),
                    "created_tick": live.get("created_tick", self._total_ticks),
                    "updated_at": datetime.now().isoformat(),
                }
                del self._hot[key]
                self._cold[key] = cold_entry
                hot_dirty = True
                cold_dirty = True
                distilled += 1
                logger.info("Distilled to COLD", key=key, summary_len=len(summary))
            if hot_dirty:
                await self._save_zone("hot", self._hot)
            if cold_dirty:
                await self._save_zone("cold", self._cold)
            return distilled
        finally:
            self._release_write()

    # ================= §8/§14: forget =================

    async def forget(self, key: str) -> bool:
        """ADR-014: Точечное ФИЗИЧЕСКОЕ удаление по ключу.
        Амнистия не действует: осознанное действие. Удаляет из любой зоны."""
        await self._acquire_write()
        try:
            if key in self._tgs:
                del self._tgs[key]
                await self._save_zone("tgs", self._tgs)
                logger.info("Forget: physically deleted from TGS", key=key)
                return True
            if key in self._hot:
                del self._hot[key]
                await self._save_zone("hot", self._hot)
                logger.info("Forget: physically deleted from HOT", key=key)
                return True
            if key in self._cold:
                del self._cold[key]
                await self._save_zone("cold", self._cold)
                logger.info("Forget: physically deleted from COLD", key=key)
                return True
            return False
        finally:
            self._release_write()

    async def redistill_summaries(
        self, topic: str, rewrite_func: Callable[[str, str], Awaitable[str]]
    ) -> None:
        """ADR-014: Принудительная дистилляция dialogue_summary после forget.
        Новое саммари наследует score старого. Если новое саммари пустое —
        запись удаляется целиком."""
        await self._acquire_write()
        try:
            dirty_tgs = False
            dirty_hot = False
            dirty_cold = False

            async def _process_zone(zone_dict: dict, zone_name: str) -> bool:
                dirty = False
                for key in list(zone_dict.keys()):
                    if not key.startswith("dialogue_summary:"):
                        continue

                    entry = zone_dict[key]
                    value = entry.get("value")
                    if not isinstance(value, dict) or "summary" not in value:
                        continue

                    old_summary = str(value["summary"])
                    new_summary = await rewrite_func(old_summary, topic)

                    if not new_summary.strip():
                        del zone_dict[key]
                        logger.info("Redistill: deleted empty summary", key=key, zone=zone_name)
                        dirty = True
                    elif new_summary.strip() != old_summary.strip():
                        value["summary"] = new_summary.strip()
                        entry["updated_at"] = datetime.now().isoformat()
                        logger.info("Redistill: updated summary", key=key, zone=zone_name)
                        dirty = True
                return dirty

            dirty_tgs = await _process_zone(self._tgs, "tgs")
            dirty_hot = await _process_zone(self._hot, "hot")
            dirty_cold = await _process_zone(self._cold, "cold")

            if dirty_tgs:
                await self._save_zone("tgs", self._tgs)
            if dirty_hot:
                await self._save_zone("hot", self._hot)
            if dirty_cold:
                await self._save_zone("cold", self._cold)
        finally:
            self._release_write()

    async def forget_all(self, confirm: bool = False) -> dict:
        if not confirm:
            return {"rotated": 0, "amnestied": 0, "next_free_tick": 0, "confirmed": False}
        await self._acquire_write()
        try:
            rotated = 0
            amnestied = 0
            next_free_tick = 0
            cold_before = set(self._cold.keys())
            hot_before = set(self._hot.keys())

            def _immune(entry_key: str, entry: dict) -> bool:
                nonlocal amnestied, next_free_tick
                if not entry_key.startswith("user_fact:"):
                    return False
                created = entry.get("created_tick", 0)
                if self._total_ticks - created < FACT_IMMUNE_TICKS:
                    amnestied += 1
                    next_free_tick = max(next_free_tick, created + FACT_IMMUNE_TICKS)
                    return True
                return False

            for key, entry in list(self._tgs.items()):
                if _immune(key, entry):
                    continue
                entry["score"] = TGS_EVICT_SCORE
                entry["shield"] = False
                entry["no_compress"] = False
                del self._tgs[key]
                self._hot[key] = entry
                rotated += 1

            for key in list(self._hot.keys()):
                if key not in hot_before:
                    continue
                entry = self._hot[key]
                if _immune(key, entry):
                    continue
                entry["score"] = round(entry.get("score", 0) - 50.0, 1)
                entry["is_cold"] = True
                summary = self._extract_fallback_summary(entry.get("value"))
                cold_entry = dict(entry)
                cold_entry["value"] = None
                cold_entry["summary"] = summary or "Факты диалога не извлечены."
                cold_entry["no_compress"] = False
                del self._hot[key]
                if cold_entry["score"] <= DELETE_BELOW:
                    logger.info("Forget all: deleted outright", key=key)
                else:
                    self._cold[key] = cold_entry
                rotated += 1

            for key in list(self._cold.keys()):
                if key not in cold_before:
                    continue
                entry = self._cold[key]
                if _immune(key, entry):
                    continue
                del self._cold[key]
                rotated += 1

            self._distill_queue.clear()
            await self._save_all()
            logger.info(
                "Forget all done",
                rotated=rotated,
                amnestied=amnestied,
                next_free_tick=next_free_tick,
            )
            return {
                "rotated": rotated,
                "amnestied": amnestied,
                "next_free_tick": next_free_tick,
                "confirmed": True,
            }
        finally:
            self._release_write()

    # ================= точный recall =================

    async def recall(self, key: str) -> Any | None:
        await self._acquire_write()
        try:
            entry = self._find_entry_unlocked(key)
            if not entry:
                return None
            zone = self._get_zone_unlocked(key)

            if zone == "cold":
                self._revive_entry(entry, HEAT_RECALL, key)
                del self._cold[key]
                await self._save_zone("cold", self._cold)
                self._hot[key] = entry
                await self._save_zone("hot", self._hot)
                self._try_unprotect(entry, key)
                await self._maybe_promote_to_tgs(key)
                return entry.get("value")

            self._apply_heat(entry, HEAT_RECALL, key)
            self._try_unprotect(entry, key)
            if zone == "tgs":
                await self._save_zone("tgs", self._tgs)
            else:
                await self._save_zone("hot", self._hot)
                await self._maybe_promote_to_tgs(key)
            return entry.get("value")
        finally:
            self._release_write()

    # ================= поиск =================

    async def recall_memory(self, query: str) -> dict:
        if not query:
            return {"found": False, "facts": []}

        self._ticks_since_recall = 0

        await self._acquire_read()
        try:
            query_words = set(re.findall(r"\w+", query.lower()))

            def _entry_text(entry: dict) -> str:
                value = entry.get("value")
                parts: list[str] = []
                if value is not None:
                    if isinstance(value, str):
                        parts.append(value)
                    elif isinstance(value, dict):
                        if "summary" in value:
                            parts.append(str(value["summary"]))
                        elif "user" in value and "assistant" in value:
                            parts.append(
                                str(value.get("user", "")) + " " + str(value.get("assistant", ""))
                            )
                        else:
                            parts.append(json.dumps(value, ensure_ascii=False))
                    else:
                        parts.append(str(value))
                summary = entry.get("summary")
                if summary:
                    parts.append(str(summary))
                return " ".join(parts).lower()

            results: list[dict] = []
            for zone_name, zone_dict in (
                ("tgs", self._tgs),
                ("hot", self._hot),
                ("cold", self._cold),
            ):
                for key, entry in zone_dict.items():
                    if any(w in _entry_text(entry) for w in query_words):
                        results.append(
                            {
                                "key": key,
                                "zone": zone_name,
                                "score": entry.get("score", 0),
                                "value": entry.get("value"),
                                "summary": entry.get("summary", ""),
                            }
                        )

            results.sort(key=lambda x: x["score"], reverse=True)
            return {
                "found": len(results) > 0,
                "facts": results[:5],
                "total_found": len(results),
            }
        finally:
            self._release_read()

    # ================= служебное =================

    @staticmethod
    def _extract_fallback_summary(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            if "summary" in value:
                return str(value["summary"])
            if "user" in value and "assistant" in value:
                return (
                    f"Пользователь спрашивал: {str(value.get('user', ''))[:150]}. "
                    f"Ответ ассистента: {str(value.get('assistant', ''))[:150]}."
                )
        return str(value)[:300]

    def __len__(self) -> int:
        return len(self._tgs) + len(self._hot) + len(self._cold)

    def get_stats(self) -> dict[str, Any]:
        return {
            "tgs": len(self._tgs),
            "hot": len(self._hot),
            "cold": len(self._cold),
            "total": len(self),
            "total_ticks": self._total_ticks,
            "distill_queue": len(self._distill_queue),
            "active_mode": self._ticks_since_recall >= ACTIVE_MODE_WINDOW,
        }
