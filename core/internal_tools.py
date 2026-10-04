"""Internal tools for Vasily AI agent (ADR-013 aware).

6.2: recall_memory heats found facts via memory.heat_facts() (§9);
remember_fact uses explicit remember_user_fact (§8, score 40 + амнистия).
Thinking-strip + whole-word dedup from stage 3.1 preserved.
"""

import asyncio
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.base_tool import BaseTool
from core.logging_config import get_logger
from core.plugin_types import make_error
from integrations.ollama_client import OllamaClient

# блок: LLM-ранжирование кандидатов (Задача №3, вариант B).
# почему: одна точка правды — memory/ranking.py; RecallMemoryTool
# и AgentCore._llm_filter_forget оба ходят туда.
from memory.ranking import rank_for_recall

logger = get_logger("core", "InternalTools")


class RecallMemoryTool(BaseTool):
    """Search facts across ALL zones (ADR-013 §9)."""

    name = "recall_memory"
    description = (
        "Search for facts, user preferences, or past dialogue in the agent's memory. "
        "Use this ONLY when the user asks about something they discussed before. "
        "CRITICAL RULE: If the tool returns {'found': False, 'facts': []}, DO NOT retry with different keywords. "
        "Immediately provide a final answer stating that you do not have this information in memory, "
        "or ask the user to provide the details."
    )
    version = "2.0.0"

    def __init__(self, memory_manager=None, llm_client=None):
        self.memory = memory_manager
        self.llm_client = llm_client

    async def _expand_query(self, query: str) -> str:
        """Query expansion via LLM (0 MB VRAM)."""
        if self.llm_client is None:
            return query
        prompt = (
            "Ты — генератор синонимов для поискового запроса. "
            "Верни ТОЛЬКО 3-5 ключевых слов-синонимов или связанных понятий "
            "на русском языке через пробел. Без исходного запроса, без объяснений, "
            "без кавычек и запятых.\n"
            f"Запрос: '{query}'"
        )
        try:
            response = await asyncio.wait_for(
                self.llm_client.generate(prompt, temperature=0.1), timeout=5.0
            )
            expanded = response.get("response", "")
            query_words = set(re.findall(r"\w+", query.lower()))
            words = [
                w for w in re.findall(r"\w+", expanded.lower()) if w not in query_words
            ][:6]
            if words:
                return f"{query} {' '.join(words)}"
        except TimeoutError:
            logger.warning("Query expansion timed out, using raw query")
        except Exception as e:
            logger.warning("Query expansion failed, using raw query", error=str(e))
        return query

    async def _execute(
        self, query: str = "", limit: int = 3, **kwargs
    ) -> dict[str, Any]:
        if self.memory is None:
            return make_error(
                "backend_unavailable",
                "Memory manager not initialized",
                "The agent is not ready. Please try again later.",
            )
        if not query or not query.strip():
            return {
                "found": False,
                "facts": [],
                "error": "Query is required. Please provide keywords to search for.",
            }

        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 3
        limit = min(max(limit, 1), 10)

        expanded_query = await self._expand_query(query)
        result = await self.memory.recall_memory(expanded_query)

        if result.get("found") and result.get("facts"):
            # блок: total_found — сколько пришло из retrieval ДО ранжирования.
            # почему: это метрика «сколько вообще подошло по substring».
            # После LLM-ранжирования в result["facts"] останется меньше —
            # это видно отдельно, не смешиваем два числа.
            result["total_found"] = len(result["facts"])

            # блок: LLM-ранжирование между retrieval и gate (Задача №3).
            # почему: substring-поиск даёт ложные срабатывания ("кот" в
            # "который") и пропускает синонимы ("ноутбук" для "компьютер").
            # LLM судит по смыслу — без эмбеддингов, но с реальным
            # пониманием. При сбое rank_for_recall вернёт всех кандидатов
            # (on_error="all"), не теряем данные.
            if self.llm_client is not None:
                result["facts"] = await rank_for_recall(
                    self.llm_client,
                    result["facts"],
                    query=query,
                )

            # блок: gate — обрезаем до limit РОВНО тех, что прошли ранжирование.
            # почему: сначала LLM-фильтр, потом лимит. Иначе релевантный
            # факт с низким score мог бы не попасть в топ-3 из-за
            # score-сортировки. Порядок важен: rank выше, limit ниже.
            result["facts"] = result["facts"][:limit]

            # блок: heat_facts получает ключи РОВНО показанных фактов.
            # почему: инвариант «нагрев = показ» (ADR-013 §9). После
            # LLM-фильтра греем только реально релевантные — мусор
            # остаётся холодным и умирает естественным путём.
            heated_keys = [f["key"] for f in result["facts"]]
            try:
                await self.memory.heat_facts(heated_keys)
            except Exception as e:
                # блок: нагрев не критичен для ответа — не роняем запрос.
                logger.warning("heat_facts failed (non-fatal)", error=str(e))

            result["expanded_query_used"] = expanded_query
        else:
            result["total_found"] = 0
            result["expanded_query_used"] = expanded_query
        return result

    def _get_parameters(self) -> dict[str, Any]:
        return {
            "query": {
                "type": "string",
                "description": "Keywords to search for in memory",
                "required": True,
            },
            "limit": {
                "type": "integer",
                "description": "Max results (1-10, default 3)",
                "required": False,
            },
        }


class RememberFactTool(BaseTool):
    """Save explicit user facts (ADR-013 §8: score 40, 10-tick amnesty)."""

    name = "remember_fact"
    description = (
        "Save an important fact to agent's memory immediately. "
        "Use ONLY when user explicitly says 'запомни' or 'save this'. "
        "CRITICAL: Do NOT use for writing files. Use write_file for that."
    )
    version = "3.0.0"

    def __init__(self, memory_manager=None, llm_client=None):
        self.memory = memory_manager
        self.llm_client = llm_client

    async def _execute(self, fact: str = "", **kwargs) -> dict[str, Any]:
        if self.memory is None:
            return make_error(
                "backend_unavailable",
                "Memory manager not initialized",
                "The agent is not ready. Please try again later.",
            )
        if not fact or not fact.strip():
            return {
                "status": "error",
                "message": "Fact is required. Please provide what you want me to remember.",
            }

        clean_fact = fact.strip()[:2000]
        words = re.findall(r"\w+", clean_fact.lower())
        search_query = " ".join(words)[:150]

        check = await self.memory.recall_memory(search_query)
        if check.get("found") and check.get("facts"):
            for existing in check["facts"][:2]:
                existing_text = str(
                    existing.get("value") or existing.get("summary", "")
                )
                if len(existing_text) < 5:
                    continue
                verdict = await self._similarity_verdict(clean_fact, existing_text)
                if verdict == "ДУБЛЬ":
                    return {
                        "status": "already_exists",
                        "message": (
                            f"Этот факт уже сохранён (ключ: {existing['key']}). "
                            "Не создавай дубликат — ответь, что уже знаешь."
                        ),
                        "existing_fact": existing_text,
                    }
                if verdict == "ПРОТИВОРЕЧИЕ":
                    # блок: проверяем результат admission gate.
                    # почему: если в новом значении секрет — manager вернёт
                    # {"stored": False, "reason": ...}. Не записываем,
                    # сообщаем LLM причину, чтобы она сказала пользователю.
                    upd = await self.memory.remember_user_fact(
                        existing["key"], clean_fact
                    )
                    if not upd.get("stored", True):
                        return {
                            "status": "rejected",
                            "message": (
                                f"Не сохранил: в новом значении есть "
                                f"{upd.get('reason')}. Скажи пользователю, "
                                f"что не записал — там пароль или токен."
                            ),
                            "reason": upd.get("reason"),
                        }
                    return {
                        "status": "updated",
                        "message": f"Факт обновлён: {clean_fact[:100]}",
                        "key": existing["key"],
                    }
                if verdict == "ДОПОЛНЕНИЕ":
                    merged = await self._merge_facts(clean_fact, existing_text)
                    # блок: gate проверяется на СЛИТОМ тексте, а не на clean_fact.
                    # почему: merge мог сам вклеить секрет из старого факта
                    # (если тот был сохранён ДО включения gate). Проверяем
                    # итоговое значение, которое реально пойдёт в память.
                    mrg = await self.memory.remember_user_fact(existing["key"], merged)
                    if not mrg.get("stored", True):
                        return {
                            "status": "rejected",
                            "message": (
                                f"Не сохранил: в объединённом факте есть "
                                f"{mrg.get('reason')}. Скажи пользователю, "
                                f"что не записал — там пароль или токен."
                            ),
                            "reason": mrg.get("reason"),
                        }
                    return {
                        "status": "merged",
                        "message": f"Факт дополнен: {merged[:150]}",
                        "key": existing["key"],
                    }

        key = f"user_fact:{uuid.uuid4().hex[:8]}"
        # блок: проверяем результат записи.
        # почему: admission gate мог отклонить факт. Возвращаем LLM
        # понятный статус "rejected" с причиной — модель скажет
        # пользователю, почему не сохранила.
        store = await self.memory.remember_user_fact(key, clean_fact)
        if not store.get("stored", True):
            return {
                "status": "rejected",
                "message": (
                    f"Не сохранил: в факте есть {store.get('reason')}. "
                    f"Скажи пользователю, что не записал — там пароль или токен."
                ),
                "reason": store.get("reason"),
            }
        return {
            "status": "success",
            "message": f"Факт сохранён: {clean_fact[:100]}",
            "key": key,
        }

    async def _similarity_verdict(self, new_fact: str, existing_fact: str) -> str:
        """'ДУБЛЬ' | 'ДОПОЛНЕНИЕ' | 'ПРОТИВОРЕЧИЕ' | 'НЕТ'. Thinking stripped."""
        if self.llm_client is None:
            return "НЕТ"
        prompt = (
            "Сравни два утверждения.\n"
            f"A: {new_fact}\n"
            f"B: {existing_fact}\n"
            "Верни ровно одно слово-вердикт:\n"
            "ДУБЛЬ — одно и то же утверждение\n"
            "ДОПОЛНЕНИЕ — A расширяет B без противоречий\n"
            "ПРОТИВОРЕЧИЕ — A противоречит B или заменяет\n"
            "НЕТ — разные факты\n"
            "Ответ — ровно одно слово, без размышлений."
        )
        try:
            response = await asyncio.wait_for(
                self.llm_client.generate(prompt, temperature=0.0), timeout=10.0
            )
            _, clean = OllamaClient.extract_thinking_and_answer(
                response.get("response", "")
            )
            answer_words = set(re.findall(r"\w+", clean.upper()))
            for word in ("ПРОТИВОРЕЧИЕ", "ДОПОЛНЕНИЕ", "ДУБЛЬ"):
                if word in answer_words:
                    return word
            return "НЕТ"
        except TimeoutError:
            logger.warning("Similarity verdict timed out, treating fact as new")
            return "НЕТ"
        except Exception as e:
            logger.warning(
                "Similarity verdict failed, treating fact as new", error=str(e)
            )
            return "НЕТ"

    async def _merge_facts(self, new_fact: str, existing_fact: str) -> str:
        if self.llm_client is None:
            return f"{existing_fact}. {new_fact}"
        prompt = (
            "Объедини факт B с новой информацией из A в один краткий факт. "
            "Если есть противоречие — верна информация из A. "
            "Верни только итоговый факт без пояснений и без размышлений.\n"
            f"A: {new_fact}\nB: {existing_fact}"
        )
        try:
            response = await asyncio.wait_for(
                self.llm_client.generate(prompt, temperature=0.1), timeout=15.0
            )
            _, merged = OllamaClient.extract_thinking_and_answer(
                response.get("response", "")
            )
            merged = merged.strip()
            if merged and len(merged) < 500:
                return merged
        except TimeoutError:
            logger.warning("Fact merge timed out, using concatenation")
        except Exception as e:
            logger.warning("Fact merge failed, using concatenation", error=str(e))
        return f"{existing_fact}. {new_fact}"

    def _get_parameters(self) -> dict[str, Any]:
        return {
            "fact": {
                "type": "string",
                "description": "The fact to remember",
                "required": True,
            }
        }


class ListFilesTool(BaseTool):
    """List files in workspace/reading (unchanged, path-traversal safe)."""

    name = "list_files"
    description = (
        "List all files in the workspace/reading directory. "
        "Use this BEFORE reading a file if you don't know the exact filename. "
        "Returns a list of files with their sizes and modification times. "
        "Example: list_files()"
    )
    version = "1.1.0"

    def __init__(self, base_dir=None):
        if base_dir is None:
            self.base_dir = Path(__file__).parent.parent / "workspace" / "reading"
        else:
            self.base_dir = Path(base_dir)

    async def _execute(self, path: str = "", **kwargs) -> dict[str, Any]:
        target_dir = Path(path) if path else self.base_dir
        if not target_dir.is_absolute():
            target_dir = self.base_dir / target_dir
        try:
            target_dir.resolve().relative_to(self.base_dir.resolve())
        except ValueError:
            return make_error(
                "invalid_url",
                f"Path '{target_dir}' is outside workspace/reading directory.",
                "Use a path within workspace/reading/.",
            )
        if not target_dir.exists():
            return make_error(
                "invalid_url",
                f"Directory '{target_dir}' does not exist.",
                "Check that the directory exists and try again.",
            )
        if not target_dir.is_dir():
            return make_error(
                "invalid_url",
                f"Path '{target_dir}' is not a directory.",
                "Provide a directory path.",
            )
        files = []
        try:
            for item in target_dir.iterdir():
                if item.is_file():
                    files.append(
                        {
                            "name": item.name,
                            "size_bytes": item.stat().st_size,
                            "modified": datetime.fromtimestamp(
                                item.stat().st_mtime
                            ).isoformat(),
                        }
                    )
        except PermissionError:
            return make_error(
                "invalid_url",
                f"Permission denied for directory '{target_dir}'.",
                "The directory exists but cannot be read.",
            )
        files.sort(key=lambda x: x["name"])
        return {
            "status": "success",
            "path": str(target_dir),
            "count": len(files),
            "files": files,
        }

    def _get_parameters(self) -> dict[str, Any]:
        return {
            "path": {
                "type": "string",
                "description": "Optional subdirectory within workspace/reading.",
                "required": False,
            },
        }
