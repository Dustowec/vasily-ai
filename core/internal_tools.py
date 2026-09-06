"""Internal tools for Vasily AI agent.

These tools are registered by AgentCore and available to the ReAct loop
as built-in plugins. They are not loaded from the plugins/ directory.
Stage 3: LLM-based semantic dedup (ДУБЛЬ/ДОПОЛНЕНИЕ/ПРОТИВОРЕЧИЕ),
uuid fact keys, hardened query expansion, total_found fix.
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

logger = get_logger("core", "InternalTools")


class RecallMemoryTool(BaseTool):
    """Tool to search facts in agent's memory (HOT and COLD zones)."""

    name = "recall_memory"
    description = (
        "Search for facts, user preferences, or past dialogue in the agent's memory. "
        "Use this ONLY when the user asks about something they discussed before. "
        "CRITICAL RULE: If the tool returns {'found': False, 'facts': []}, DO NOT retry with different keywords. "
        "Immediately provide a final answer stating that you do not have this information in memory, "
        "or ask the user to provide the details."
    )
    version = "1.3.0"

    def __init__(self, memory_manager=None, llm_client=None):
        self.memory = memory_manager
        self.llm_client = llm_client

    async def _expand_query(self, query: str) -> str:
        """Расширяет запрос синонимами через LLM (0 МБ VRAM overhead)."""
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
            words = [w for w in re.findall(r"\w+", expanded.lower()) if w not in query_words][:6]
            if words:
                return f"{query} {' '.join(words)}"
        except TimeoutError:
            logger.warning("Query expansion timed out, using raw query")
        except Exception as e:
            logger.warning("Query expansion failed, using raw query", error=str(e))

        return query

    async def _execute(self, query: str = "", limit: int = 3, **kwargs) -> dict[str, Any]:
        """Поиск фактов в памяти с предварительным расширением запроса через LLM."""
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
            # Fix: report the REAL total before slicing to limit.
            result["total_found"] = len(result["facts"])
            result["facts"] = result["facts"][:limit]
            result["expanded_query_used"] = expanded_query
        else:
            result["total_found"] = 0
            result["expanded_query_used"] = expanded_query

        return result

    def _get_parameters(self) -> dict[str, Any]:
        return {
            "query": {
                "type": "string",
                "description": "Keywords to search for in memory (e.g., 'имя главного героя', 'предпочтения пользователя')",
                "required": True,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (1-10, default: 3)",
                "required": False,
            },
        }


class RememberFactTool(BaseTool):
    """Tool to save explicit facts to memory immediately."""

    name = "remember_fact"
    description = (
        "Save an important fact to agent's memory immediately. "
        "Use ONLY when user explicitly says 'запомни' or 'save this'. "
        "Examples: 'Запомни: моего кота зовут Барсик', 'Save this: I prefer Python'. "
        "CRITICAL: Do NOT use for writing files. Use write_file for that."
    )
    version = "2.0.0"

    def __init__(self, memory_manager=None, llm_client=None):
        self.memory = memory_manager
        self.llm_client = llm_client

    async def _execute(self, fact: str = "", **kwargs) -> dict[str, Any]:
        """Save fact to HOT memory with LLM-based semantic dedup."""
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

        # === СЕМАНТИЧЕСКАЯ ПРОВЕРКА НА ДУБЛИКАТ (LLM-вердикт) ===
        # Консервативный fallback: если LLM недоступен, факт сохраняется
        # как новый (потерять факт юзера хуже, чем сохранить дубликат).
        check = await self.memory.recall_memory(clean_fact[:50])
        if check.get("found") and check.get("facts"):
            for existing in check["facts"][:2]:
                existing_text = str(existing.get("value") or existing.get("summary", ""))
                if len(existing_text) < 5:
                    continue
                verdict = await self._similarity_verdict(clean_fact, existing_text)
                if verdict == "ДУБЛЬ":
                    return {
                        "status": "already_exists",
                        "message": (
                            f"Этот факт уже сохранён в памяти (ключ: {existing['key']}). "
                            "Не создавай дубликат. Просто ответь пользователю, "
                            "что ты это уже знаешь."
                        ),
                        "existing_fact": existing_text,
                    }
                if verdict == "ПРОТИВОРЕЧИЕ":
                    # Новая информация заменяет устаревшую: перезапись по тому же ключу
                    await self.memory.remember(existing["key"], clean_fact, complex_query=False)
                    return {
                        "status": "updated",
                        "message": f"Факт обновлён (было устаревшее): {clean_fact[:100]}",
                        "key": existing["key"],
                    }
                if verdict == "ДОПОЛНЕНИЕ":
                    merged = await self._merge_facts(clean_fact, existing_text)
                    await self.memory.remember(existing["key"], merged, complex_query=False)
                    return {
                        "status": "merged",
                        "message": f"Факт дополнен: {merged[:150]}",
                        "key": existing["key"],
                    }
        # =========================================================

        # Дубликата нет — сохраняем как новый факт (uuid защищает от
        # коллизий при двух фактах в одну секунду).
        key = f"user_fact:{uuid.uuid4().hex[:8]}"
        await self.memory.remember(key, clean_fact, complex_query=len(clean_fact) > 100)

        return {
            "status": "success",
            "message": f"Факт сохранён: {clean_fact[:100]}",
            "key": key,
        }

    async def _similarity_verdict(self, new_fact: str, existing_fact: str) -> str:
        """Returns 'ДУБЛЬ' | 'ДОПОЛНЕНИЕ' | 'ПРОТИВОРЕЧИЕ' | 'НЕТ'.

        Conservative fallback: if LLM is unavailable, treat as NEW fact
        (storing a duplicate is better than losing a user's fact).
        """
        if self.llm_client is None:
            return "НЕТ"
        prompt = (
            "Сравни два утверждения.\n"
            f"A: {new_fact}\n"
            f"B: {existing_fact}\n"
            "Верни ровно одно слово-вердикт:\n"
            "ДУБЛЬ — A и B утверждают одно и то же\n"
            "ДОПОЛНЕНИЕ — A добавляет новое к B, противоречий нет\n"
            "ПРОТИВОРЕЧИЕ — A противоречит B или заменяет устаревшую информацию\n"
            "НЕТ — это разные факты\n"
            "Ответ — одним словом, без объяснений."
        )
        try:
            response = await asyncio.wait_for(
                self.llm_client.generate(prompt, temperature=0.0), timeout=10.0
            )
            answer = response.get("response", "").upper()
            for word in ("ДУБЛЬ", "ДОПОЛНЕНИЕ", "ПРОТИВОРЕЧИЕ"):
                if word in answer:
                    return word
            return "НЕТ"
        except TimeoutError:
            logger.warning("Similarity verdict timed out, treating fact as new")
            return "НЕТ"
        except Exception as e:
            logger.warning("Similarity verdict failed, treating fact as new", error=str(e))
            return "НЕТ"

    async def _merge_facts(self, new_fact: str, existing_fact: str) -> str:
        """Merge supplement into existing fact via LLM, fallback to concat."""
        if self.llm_client is None:
            return f"{existing_fact}. {new_fact}"
        prompt = (
            "Объедини факт B с новой информацией из A в один краткий факт. "
            "Если есть противоречие — верна информация из A. "
            "Верни только итоговый факт без пояснений.\n"
            f"A: {new_fact}\nB: {existing_fact}"
        )
        try:
            response = await asyncio.wait_for(
                self.llm_client.generate(prompt, temperature=0.1), timeout=15.0
            )
            merged = response.get("response", "").strip()
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
                "description": "The fact to remember (e.g., 'моего кота зовут Барсик')",
                "required": True,
            }
        }


class ListFilesTool(BaseTool):
    """Tool to list files in the workspace/reading directory."""

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
        """List files in the workspace/reading directory."""
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
                            "modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat(),
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
                "description": "Optional subdirectory within workspace/reading. Defaults to workspace/reading/.",
                "required": False,
            },
        }
