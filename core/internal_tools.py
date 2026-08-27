"""Internal tools for Vasily AI agent."""

from typing import Any

from core.base_tool import BaseTool


class RecallMemoryTool(BaseTool):
    """Tool to search facts in agent's memory (HOT and COLD zones)."""

    name = "recall_memory"
    description = (
        "Search for facts, user preferences, or past dialogue in the agent's memory. "
        "Use this when the user asks about something they discussed before, or when you need "
        "personal context about the user. Do NOT use this for general knowledge or real-world facts "
        "(use web_search instead). TGS (core identity) is always available and not searchable here."
    )
    version = "1.0.0"

    def __init__(self, memory_manager):
        self.memory = memory_manager

    async def _execute(self, query: str = "", **kwargs) -> dict[str, Any]:
        """Search memory by keywords."""
        if not query:
            return {"found": False, "facts": [], "message": "Query is empty"}

        result = await self.memory.recall_memory(query)
        return result

    def _get_parameters(self) -> dict[str, Any]:
        return {
            "query": {
                "type": "string",
                "description": "Keywords or phrases to search for in memory",
                "required": True,
            }
        }
