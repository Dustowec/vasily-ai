"""
Base interface for all plugins.

Every plugin must inherit from BaseTool and implement:
- _execute() - main logic (called by execute() with auto-logging)
- _get_parameters() - parameter schema
"""

import time
from abc import ABC, abstractmethod
from typing import Any

from core.logging_config import get_logger


class BaseTool(ABC):
    """Abstract base class for all plugins."""

    name: str = "unnamed_tool"
    description: str = "No description provided"
    version: str = "1.0.0"

    async def execute(self, **kwargs) -> dict[str, Any]:
        """
        Execute the tool with given parameters.
        Wraps _execute() with automatic logging.
        """
        logger = get_logger("plugins", self.name)

        # Log the call
        logger.info(
            "Tool called",
            args=kwargs,
        )

        start_time = time.perf_counter()

        try:
            result = await self._execute(**kwargs)

            # Log success
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.info(
                "Tool succeeded",
                duration_ms=round(duration_ms, 2),
                result_preview=str(result)[:100],
            )
            return result

        except Exception as e:
            # Log failure
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "Tool failed",
                error=str(e),
                duration_ms=round(duration_ms, 2),
            )
            raise

    @abstractmethod
    async def _execute(self, **kwargs) -> dict[str, Any]:
        """
        Execute the tool with given parameters.
        Implement this method in subclasses.
        """
        pass

    def get_schema(self) -> dict[str, Any]:
        """
        Get JSON schema for this tool.
        Used by LLM to understand what tools are available.
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
        """
        pass
