"""
Plugin Registry - automatically discovers and registers plugins.

Scans the plugins/ directory and registers all tools that inherit from BaseTool.
Uses __all__ in each module for explicit registration.
"""

import importlib
import pkgutil
from pathlib import Path

from core.base_tool import BaseTool
from core.logging_config import get_logger

logger = get_logger("core", "PluginRegistry")


class PluginRegistry:
    """Registry for all plugins."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def discover_plugins(self, package_name: str = "plugins") -> None:
        """
        Automatically discover and register plugins from a package.

        Args:
            package_name: Name of the package to scan (default: "plugins")
        """
        try:
            package = importlib.import_module(package_name)
        except ImportError as e:
            logger.error("Failed to import plugins package", error=str(e))
            return

        package_dir = Path(package.__file__).parent

        # Scan all modules in the package
        for _, module_name, _ in pkgutil.iter_modules([str(package_dir)]):
            if module_name.startswith("_"):
                continue  # Skip private modules

            try:
                module = importlib.import_module(f"{package_name}.{module_name}")
                self._register_from_module(module)
            except Exception as e:
                logger.error(
                    "Failed to load plugin module",
                    module=module_name,
                    error=str(e),
                )

        logger.info(
            "Plugin discovery complete",
            total_plugins=len(self._tools),
            plugins=list(self._tools.keys()),
        )

    def _register_from_module(self, module) -> None:
        """Register all BaseTool subclasses from a module."""
        # If module has __all__, only look at those names
        tool_names = getattr(module, "__all__", None)

        if tool_names:
            for name in tool_names:
                attr = getattr(module, name, None)
                if (
                    attr is not None
                    and isinstance(attr, type)
                    and issubclass(attr, BaseTool)
                    and attr is not BaseTool
                ):
                    self.register(attr())
        else:
            # No __all__, scan all attributes
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, BaseTool) and attr is not BaseTool:
                    self.register(attr())

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        if tool.name in self._tools:
            logger.warning(
                "Plugin already registered, overwriting",
                plugin=tool.name,
            )
        self._tools[tool.name] = tool
        logger.info("Plugin registered", plugin=tool.name, version=tool.version)

    def get(self, name: str) -> BaseTool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_tools_schema(self) -> list[dict]:
        """Get schemas for all tools (for LLM)."""
        return [tool.get_schema() for tool in self._tools.values()]

    def list_tools(self) -> list[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
