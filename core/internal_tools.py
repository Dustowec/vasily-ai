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
    version = "1.0.0"

    def __init__(self, memory_manager=None):
        self.memory = memory_manager

    async def _execute(self, query: str = "", limit: int = 3, **kwargs) -> dict[str, Any]:
        """Search for facts in memory by keywords."""
        if not self.memory:
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

        # Validate and clamp limit
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 3
        limit = min(max(limit, 1), 10)

        result = await self.memory.recall_memory(query)

        # Limit results and preserve the same structure
        if result.get("found") and result.get("facts"):
            result["facts"] = result["facts"][:limit]
            result["total_found"] = len(result["facts"])
        else:
            result["total_found"] = 0

        return result

    def _get_parameters(self) -> dict[str, Any]:
        """Get parameter schema."""
        return {
            "query": {
                "type": "string",
                "description": "Keywords to search for in memory (e.g., 'user name', 'project idea', 'previous conversation about architecture')",
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
    version = "1.0.0"

    def __init__(self, memory_manager=None):
        self.memory = memory_manager

    async def _execute(self, fact: str = "", **kwargs) -> dict[str, Any]:
        """Save fact to HOT memory immediately."""
        if not self.memory:
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

        # Generate unique key with timestamp
        key = f"user_fact:{int(time.time())}"

        # Save to memory with high score
        await self.memory.remember(key, fact.strip(), complex_query=len(fact) > 100)

        return {"status": "success", "message": f"Факт сохранён: {fact[:100]}", "key": key}

    def _get_parameters(self) -> dict[str, Any]:
        """Get parameter schema."""
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

        # Безопасность: не даём вылезти за пределы workspace/reading
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

        # Сортируем по имени
        files.sort(key=lambda x: x["name"])

        return {
            "status": "success",
            "path": str(target_dir),
            "count": len(files),
            "files": files,
        }

    def _get_parameters(self) -> dict[str, Any]:
        """Get parameter schema."""
        return {
            "path": {
                "type": "string",
                "description": "Optional subdirectory within workspace/reading. Defaults to workspace/reading/.",
                "required": False,
            },
        }
