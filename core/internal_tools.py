"""Internal tools for Vasily AI agent.

These tools are registered by AgentCore and available to the ReAct loop
as built-in plugins. They are not loaded from the plugins/ directory.
"""

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core.base_tool import BaseTool
from core.plugin_types import make_error


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
    version = "1.2.0"

    def __init__(self, memory_manager=None, llm_client=None):
        self.memory = memory_manager
        self.llm_client = llm_client

    async def _expand_query(self, query: str) -> str:
        """Использует LLM для расширения запроса синонимами (0 МБ VRAM overhead)."""
        if self.llm_client is None:
            return query

        prompt = (
            "Ты — система улучшения поисковых запросов для базы знаний. "
            "Пользователь ищет факт в памяти. Твоя задача: вернуть исходный запрос и 3-5 ключевых слов-синонимов или связанных понятий на русском языке, разделенных пробелом. "
            "Никаких объяснений, никаких кавычек, только слова через пробел. "
            f"Запрос: '{query}'"
        )
        try:
            response = await self.llm_client.generate(prompt, temperature=0.1)
            expanded = response.get("response", "").strip()
            if expanded and len(expanded) < 150:
                return f"{query} {expanded}"
        except Exception:
            pass
        
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
            result["facts"] = result["facts"][:limit]
            result["total_found"] = len(result["facts"])
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
    version = "1.1.0"  # Обновлено: добавлена реальная защита от дубликатов

    def __init__(self, memory_manager=None):
        self.memory = memory_manager

    async def _execute(self, fact: str = "", **kwargs) -> dict[str, Any]:
        """Save fact to HOT memory immediately with duplicate protection."""
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

        clean_fact = fact.strip()

        # === АРХИТЕКТУРНАЯ ЗАЩИТА ОТ ДУБЛИКАТОВ ===
        # Поскольку recall_memory теперь использует LLM expansion, он находит семантически похожие факты.
        # Мы делаем быстрый поиск и проверяем, не является ли найденный факт дубликатом по длине.
        search_query = clean_fact[:50]
        check = await self.memory.recall_memory(search_query)
        
        if check.get("found") and check.get("facts"):
            existing = check["facts"][0]
            existing_text = str(existing.get("value") or existing.get("summary", ""))
            
            # Если длины текстов сопоставимы (разница не более чем в 2.5 раза), считаем это дубликатом
            if len(existing_text) > 10 and len(clean_fact) > 10:
                ratio = min(len(existing_text), len(clean_fact)) / max(len(existing_text), len(clean_fact))
                if ratio > 0.4:  # Тексты примерно одного порядка длины
                    return {
                        "status": "already_exists",
                        "message": f"ВНИМАНИЕ: Этот факт уже сохранён в памяти (ключ: {existing['key']}). НЕ вызывай этот инструмент повторно. Просто ответь пользователю, что ты это уже знаешь, и не создавай дубликат.",
                        "existing_fact": existing_text
                    }
        # ==========================================

        # Если дубликата нет, сохраняем как обычно
        key = f"user_fact:{int(time.time())}"
        await self.memory.remember(key, clean_fact, complex_query=len(clean_fact) > 100)

        return {"status": "success", "message": f"Факт сохранён: {clean_fact[:100]}", "key": key}

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
    version = "1.0.0"

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
        for item in target_dir.iterdir():
            if item.is_file():
                files.append(
                    {
                        "name": item.name,
                        "size_bytes": item.stat().st_size,
                        "modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat(),
                    }
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