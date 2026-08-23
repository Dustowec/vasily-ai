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

# Файлы хранения
TGS_FILE = "data/tgs_memory.json"
HOT_FILE = "data/tg_hot_memory.json"
COLD_FILE = "data/tg_cold_memory.json"
TEMP_SUFFIX = ".tmp"


class GradientMemory:
    """Градиентно-сессионная память с динамическим охлаждением."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.tgs_file = self.data_dir / "tgs_memory.json"
        self.hot_file = self.data_dir / "tg_hot_memory.json"
        self.cold_file = self.data_dir / "tg_cold_memory.json"

        self._lock = asyncio.Lock()
        self._session_requests = 0
        self._session_count = 0

        self._tgs: dict[str, dict] = {}
        self._hot: dict[str, dict] = {}
        self._cold: dict[str, dict] = {}

        self._load_all()

    # ==================== ЗАГРУЗКА / СОХРАНЕНИЕ ====================

    def _load_all(self) -> None:
        """Загрузить все три зоны из файлов."""
        self._tgs = self._load_zone(self.tgs_file)
        self._hot = self._load_zone(self.hot_file)
        self._cold = self._load_zone(self.cold_file)
        logger.info(
            "GradientMemory loaded", tgs=len(self._tgs), hot=len(self._hot), cold=len(self._cold)
        )

    def _load_zone(self, path: Path) -> dict[str, dict]:
        """Загрузить одну зону из файла."""
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load memory zone", path=str(path), error=str(e))
            return {}

    async def _save_zone(self, zone: str, data: dict[str, dict]) -> None:
        """Атомарно сохранить зону через временный файл."""
        path_map = {
            "tgs": self.tgs_file,
            "hot": self.hot_file,
            "cold": self.cold_file,
        }
        path = path_map.get(zone)
        if not path:
            raise ValueError(f"Unknown zone: {zone}")

        temp_path = path.with_suffix(path.suffix + TEMP_SUFFIX)

        try:
            # Пишем во временный файл
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)

            # Переименовываем (атомарно)
            os.replace(temp_path, path)

            logger.debug("Zone saved", zone=zone, count=len(data))
        except Exception as e:
            logger.error("Failed to save zone", zone=zone, error=str(e))
            # Пытаемся удалить временный файл, если он остался
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            raise

    async def _save_all(self) -> None:
        """Сохранить все три зоны."""
        await self._save_zone("tgs", self._tgs)
        await self._save_zone("hot", self._hot)
        await self._save_zone("cold", self._cold)

    # ==================== ОСНОВНЫЕ ОПЕРАЦИИ ====================

    async def remember(self, key: str, value: Any, complex_query: bool = False) -> None:
        """Сохранить запись в памяти (нагрев +10.0)."""
        async with self._lock:
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

            # Проверяем, есть ли уже такая тема
            existing = self._find_entry(key)
            if existing:
                entry["score"] = existing.get("score", 0) + REINFORCE_HEAT
                entry["summary"] = existing.get("summary")
                entry["created_at"] = existing.get("created_at", datetime.now().isoformat())
                # Если тема была в Cold, снимаем флаг
                entry["is_cold"] = False

            # Если ключ уже есть в TGS, обновляем его там
            if key in self._tgs:
                self._tgs[key] = entry
                await self._save_zone("tgs", self._tgs)
                logger.info("Remember: updated in TGS", key=key, score=entry["score"])
                return

            # Если ключ есть в Hot
            if key in self._hot:
                self._hot[key] = entry
                await self._save_zone("hot", self._hot)
                logger.info("Remember: updated in HOT", key=key, score=entry["score"])
                return

            # Если ключ есть в Cold — извлекаем оттуда
            if key in self._cold:
                entry["protected"] = True
                entry["summary"] = self._cold[key].get("summary")
                del self._cold[key]
                await self._save_zone("cold", self._cold)

            # Если ключа нет — создаём новую запись
            self._hot[key] = entry
            await self._save_zone("hot", self._hot)

            # Проверяем, не нужно ли переместить в TGS
            await self._check_promote_to_tgs(key)
            logger.info("Remember: stored", key=key, score=entry["score"])

    async def recall(self, key: str) -> Any | None:
        """Вспомнить запись (нагрев +5.0)."""
        async with self._lock:
            entry = self._find_entry(key)
            if not entry:
                return None

            # Если запись в Cold — извлекаем с защитой
            if key in self._cold:
                entry = self._cold[key].copy()
                entry["score"] = 10.0
                entry["protected"] = True
                entry["is_cold"] = False
                entry["updated_at"] = datetime.now().isoformat()

                # Удаляем из Cold
                del self._cold[key]
                await self._save_zone("cold", self._cold)

                # Помещаем в Hot
                self._hot[key] = entry
                await self._save_zone("hot", self._hot)
                logger.info("Recall: moved from COLD to HOT", key=key, score=entry["score"])
                return entry.get("value")

            # Если в Hot или TGS — просто нагреваем
            entry["score"] = min(entry.get("score", 0) + REGULAR_HEAT, 100.0)
            entry["updated_at"] = datetime.now().isoformat()

            zone = self._get_zone(key)
            if zone:
                await self._save_zone(zone, getattr(self, f"_{zone}"))
                logger.info("Recall: heated", key=key, new_score=entry["score"])

            await self._check_promote_to_tgs(key)
            return entry.get("value")

    def _find_entry(self, key: str) -> dict | None:
        """Найти запись по ключу в любой зоне."""
        if key in self._tgs:
            return self._tgs[key]
        if key in self._hot:
            return self._hot[key]
        if key in self._cold:
            return self._cold[key]
        return None

    def _get_zone(self, key: str) -> str | None:
        """Определить, в какой зоне находится ключ."""
        if key in self._tgs:
            return "tgs"
        if key in self._hot:
            return "hot"
        if key in self._cold:
            return "cold"
        return None

    # ==================== ПЕРЕМЕЩЕНИЕ МЕЖДУ ЗОНАМИ ====================

    async def _check_promote_to_tgs(self, key: str) -> None:
        """Проверить, нужно ли переместить запись в TGS."""
        entry = self._find_entry(key)
        if not entry:
            return

        if entry.get("score", 0) > TGS_THRESHOLD:
            # Если уже в TGS — просто обновляем
            if key in self._tgs:
                self._tgs[key] = entry
                await self._save_zone("tgs", self._tgs)
                return

            # Удаляем из Hot или Cold
            if key in self._hot:
                del self._hot[key]
                await self._save_zone("hot", self._hot)
            elif key in self._cold:
                del self._cold[key]
                await self._save_zone("cold", self._cold)

            # Добавляем в TGS
            entry["shield"] = True
            self._tgs[key] = entry
            await self._save_zone("tgs", self._tgs)
            logger.info("Promoted to TGS", key=key, score=entry["score"])

    async def _check_demote_from_tgs(self, key: str) -> None:
        """Проверить, нужно ли переместить запись из TGS в Hot."""
        if key not in self._tgs:
            return

        entry = self._tgs[key]
        if entry.get("score", 0) <= TGS_THRESHOLD:
            entry["shield"] = False
            del self._tgs[key]
            await self._save_zone("tgs", self._tgs)

            self._hot[key] = entry
            await self._save_zone("hot", self._hot)
            logger.info("Demoted from TGS to HOT", key=key, score=entry["score"])

    # ==================== ОСТЫВАНИЕ ====================

    async def decay(self, count_requests: int) -> None:
        """Применить остывание ко всем записям (кроме TGS)."""
        async with self._lock:
            decay_actual = max(0.01, 0.1 - (count_requests * 0.0003))

            # Остывание Hot
            changed = False
            for key, entry in list(self._hot.items()):
                if entry.get("protected", False):
                    # Protected не защищает от остывания
                    pass
                new_score = entry.get("score", 0) - decay_actual
                entry["score"] = max(new_score, DELETE_THRESHOLD)
                entry["updated_at"] = datetime.now().isoformat()
                changed = True

                # Проверяем, не нужно ли сжать
                if COMPRESSION_RANGE_HIGH <= entry["score"] <= COMPRESSION_RANGE_LOW:
                    # Компрессия запускается отдельно
                    pass

                # Проверяем, не нужно ли удалить
                if entry["score"] <= DELETE_THRESHOLD:
                    del self._hot[key]
                    logger.info("Decay: deleted from HOT", key=key)
                    changed = True

            if changed:
                await self._save_zone("hot", self._hot)

            # Остывание Cold (медленнее)
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

            # TGS не остывает

    async def session_close(self) -> None:
        """Штраф при закрытии/перезапуске сессии (-2.0 ко всем, кроме TGS)."""
        async with self._lock:
            # Hot
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

            # Cold
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

            # TGS не штрафуется

            self._session_count += 1
            logger.info("Session close applied", session=self._session_count)

    # ==================== КОМПРЕССИЯ ====================

    async def compress_cycle(self, compressor: Callable[[Any], Awaitable[str]]) -> int:
        """Сжать записи в диапазоне 5..-4."""
        async with self._lock:
            compressed = 0

            for key, entry in list(self._hot.items()):
                score = entry.get("score", 0)

                # Проверяем диапазон
                if not (COMPRESSION_RANGE_HIGH <= score <= COMPRESSION_RANGE_LOW):
                    continue

                # Проверяем protected
                if entry.get("protected", False):
                    logger.debug("Compression skipped: protected", key=key)
                    continue

                # Проверяем, есть ли уже summary
                existing_summary = entry.get("summary")
                if existing_summary:
                    # Используем старый summary
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
                    # Вызываем LLM для создания summary
                    try:
                        summary = await compressor(entry.get("value", ""))
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

                # Удаляем из Hot
                del self._hot[key]
                await self._save_zone("hot", self._hot)

                # Добавляем в Cold
                self._cold[key] = cold_entry
                await self._save_zone("cold", self._cold)

                compressed += 1
                logger.info(
                    "Compressed to COLD",
                    key=key,
                    summary_len=len(str(cold_entry.get("summary", ""))),
                )

            return compressed

    # ==================== КОМАНДЫ ЗАБЫТЬ ====================

    async def forget(self, key: str) -> bool:
        """Забыть конкретную тему (штраф -50.0 или -20.0 для TGS)."""
        async with self._lock:
            entry = self._find_entry(key)
            if not entry:
                return False

            # Если в TGS — особый случай
            if key in self._tgs:
                entry["score"] = max(entry.get("score", 0) - 20.0, 0.0)
                entry["shield"] = False
                await self._save_zone("tgs", self._tgs)

                # Перемещаем в Hot
                del self._tgs[key]
                self._hot[key] = entry
                await self._save_zone("hot", self._hot)
                logger.info("Forget: moved from TGS to HOT", key=key)
                return True

            # Для Hot и Cold — применяем -50.0
            zone = self._get_zone(key)
            if not zone:
                return False

            entry["score"] = entry.get("score", 0) - 50.0
            entry["updated_at"] = datetime.now().isoformat()

            if entry["score"] <= DELETE_THRESHOLD:
                # Удаляем запись
                if zone == "hot":
                    del self._hot[key]
                    await self._save_zone("hot", self._hot)
                elif zone == "cold":
                    del self._cold[key]
                    await self._save_zone("cold", self._cold)
                logger.info("Forget: deleted", key=key)
            else:
                # Перемещаем в Cold, если ещё не там
                if zone == "hot":
                    entry["is_cold"] = True
                    del self._hot[key]
                    self._cold[key] = entry
                    await self._save_zone("hot", self._hot)
                    await self._save_zone("cold", self._cold)
                    logger.info("Forget: moved to COLD", key=key)

            return True

    async def forget_all(self, confirm: bool = False) -> bool:
        """Забыть всё (ротация памяти). Требует двойного подтверждения."""
        if not confirm:
            return False

        async with self._lock:
            # TGS → Hot (со штрафом -20.0)
            for key, entry in list(self._tgs.items()):
                entry["score"] = max(entry.get("score", 0) - 20.0, 0.0)
                entry["shield"] = False
                del self._tgs[key]
                self._hot[key] = entry

            # Hot → Cold (компрессия без LLM)
            for key, entry in list(self._hot.items()):
                summary = (
                    entry.get("summary") or f"Compressed: {str(entry.get('value', ''))[:200]}..."
                )
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
                del self._hot[key]
                self._cold[key] = cold_entry

            # Cold → удаление (если score <= -50.0)
            for key, entry in list(self._cold.items()):
                if entry.get("score", 0) <= DELETE_THRESHOLD:
                    del self._cold[key]

            await self._save_all()
            logger.info("Forget all: rotation completed")
            return True

    # ==================== BUILD CONTEXT (ДЛЯ LLM) ====================

    async def build_context(self, query: str, max_tokens: int = 3000) -> str:
        """Собрать контекст для LLM в формате Markdown."""
        async with self._lock:
            parts = []

            # Берём топ-5 из TGS
            tgs_items = sorted(self._tgs.items(), key=lambda x: x[1].get("score", 0), reverse=True)[
                :5
            ]
            for key, entry in tgs_items:
                parts.append(f"[TGS: {key}] {self._format_value(entry)}")

            # Берём топ-10 из Hot (с protected в приоритете)
            hot_items = sorted(
                self._hot.items(),
                key=lambda x: (x[1].get("protected", False), x[1].get("score", 0)),
                reverse=True,
            )[:10]
            for key, entry in hot_items:
                parts.append(f"[HOT: {key}] {self._format_value(entry)}")

            # Берём релевантные из Cold (по ключевым словам)
            query_words = set(query.lower().split())
            cold_items = []
            for key, entry in self._cold.items():
                summary = entry.get("summary", "")
                if any(word in summary.lower() for word in query_words):
                    cold_items.append((key, entry))
            cold_items = sorted(cold_items, key=lambda x: x[1].get("score", 0), reverse=True)[:3]
            for key, entry in cold_items:
                parts.append(f"[COLD: {key}] {entry.get('summary', '')}")

            context = "\n\n".join(parts)
            return context[:max_tokens]

    @staticmethod
    def _format_value(entry: dict) -> str:
        """Форматировать значение для контекста."""
        value = entry.get("value")
        if value is None:
            return entry.get("summary", "")

        if isinstance(value, str):
            return value[:500]

        if isinstance(value, dict):
            return str(value)[:500]

        if isinstance(value, list):
            return str(value)[:500]

        return str(value)[:500]

    # ==================== ВСПОМОГАТЕЛЬНЫЕ ====================

    def __len__(self) -> int:
        return len(self._tgs) + len(self._hot) + len(self._cold)

    def get_stats(self) -> dict[str, Any]:
        """Получить статистику памяти."""
        return {
            "tgs": len(self._tgs),
            "hot": len(self._hot),
            "cold": len(self._cold),
            "total": len(self),
            "session_count": self._session_count,
            "session_requests": self._session_requests,
        }
