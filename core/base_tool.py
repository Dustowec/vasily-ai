"""
Base interface for all plugins.

Every plugin must inherit from BaseTool and implement:
- execute() - main logic
- _get_parameters() - parameter schema
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """Abstract base class for all plugins."""

    name: str = "unnamed_tool"
    description: str = "No description provided"
    version: str = "1.0.0"

    @abstractmethod
    async def execute(self, **kwargs) -> dict[str, Any]:
        """
        Execute the tool with given parameters.

        Args:
            **kwargs: Tool-specific parameters

        Returns:
            Dictionary with results
        """
        pass

    def get_schema(self) -> dict[str, Any]:
        """
        Get JSON schema for this tool.
        Used by LLM to understand what tools are available.

        Returns:
            Dictionary with tool schema
        """
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": self._get_parameters(),
        }

    @abstractmethod
    def _get_parameters(self) -> dict[str, Any]:
        """
        Get parameter schema for this tool.

        Returns:
            Dictionary with parameter definitions
        """
        pass
