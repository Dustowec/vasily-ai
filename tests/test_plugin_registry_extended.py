"""Extended tests for PluginRegistry.

Covers: discovery, registration, get, schema,
        edge cases, error handling.
"""

from unittest.mock import MagicMock, patch

from core.base_tool import BaseTool
from core.plugin_registry import PluginRegistry

# ==================== FAKE PLUGINS ====================


class FakeTool1(BaseTool):
    name = "fake_tool_1"
    description = "Fake tool 1"
    version = "1.0.0"

    async def _execute(self, **kwargs):
        return {"status": "success"}

    def _get_parameters(self):
        return {"param1": {"type": "string", "description": "param1", "required": True}}


class FakeTool2(BaseTool):
    name = "fake_tool_2"
    description = "Fake tool 2"
    version = "2.0.0"

    async def _execute(self, **kwargs):
        return {"status": "success"}

    def _get_parameters(self):
        return {"param2": {"type": "integer", "description": "param2", "required": False}}


# ==================== TEST INIT ====================


def test_init_empty():
    """PluginRegistry should start empty."""
    registry = PluginRegistry()
    assert len(registry) == 0
    assert registry.list_tools() == []


# ==================== TEST REGISTER ====================


def test_register_single_tool():
    """register should add a tool."""
    registry = PluginRegistry()
    tool = FakeTool1()
    registry.register(tool)
    assert len(registry) == 1
    assert registry.list_tools() == ["fake_tool_1"]


def test_register_multiple_tools():
    """register should add multiple tools."""
    registry = PluginRegistry()
    registry.register(FakeTool1())
    registry.register(FakeTool2())
    assert len(registry) == 2
    assert set(registry.list_tools()) == {"fake_tool_1", "fake_tool_2"}


def test_register_overwrites_duplicate():
    """register should overwrite tools with same name."""
    registry = PluginRegistry()
    registry.register(FakeTool1())
    registry.register(FakeTool1())
    assert len(registry) == 1


# ==================== TEST GET ====================


def test_get_existing_tool():
    """get should return registered tool."""
    registry = PluginRegistry()
    tool = FakeTool1()
    registry.register(tool)
    result = registry.get("fake_tool_1")
    assert result is tool


def test_get_missing_tool():
    """get should return None for missing tool."""
    registry = PluginRegistry()
    result = registry.get("nonexistent")
    assert result is None


# ==================== TEST GET_TOOLS_SCHEMA ====================


def test_get_tools_schema_empty():
    """get_tools_schema should return empty list for empty registry."""
    registry = PluginRegistry()
    assert registry.get_tools_schema() == []


def test_get_tools_schema_returns_schemas():
    """get_tools_schema should return schemas for all tools."""
    registry = PluginRegistry()
    registry.register(FakeTool1())
    registry.register(FakeTool2())

    schemas = registry.get_tools_schema()
    assert len(schemas) == 2
    names = [s["name"] for s in schemas]
    assert "fake_tool_1" in names
    assert "fake_tool_2" in names

    # Check schema structure
    for schema in schemas:
        assert "name" in schema
        assert "description" in schema
        assert "version" in schema
        assert "parameters" in schema


# ==================== TEST CONTAINS ====================


def test_contains():
    """__contains__ should check tool existence."""
    registry = PluginRegistry()
    registry.register(FakeTool1())
    assert "fake_tool_1" in registry
    assert "fake_tool_2" not in registry


# ==================== TEST DISCOVER_PLUGINS ====================


def test_discover_plugins_import_error():
    """discover_plugins should handle import errors gracefully."""
    registry = PluginRegistry()
    with patch("importlib.import_module", side_effect=ImportError("No module")):
        registry.discover_plugins("nonexistent")
    assert len(registry) == 0


def test_discover_plugins_handles_module_import():
    """discover_plugins should handle module import."""
    registry = PluginRegistry()
    # Просто вызываем с существующим модулем plugins (он есть в проекте)
    # Если модуль есть — не падает
    registry.discover_plugins("plugins")
    # Может быть 0 или больше, но метод должен отработать без исключений


# ==================== TEST REGISTER_FROM_MODULE ====================


def test_register_from_module_with_all():
    """_register_from_module should use __all__ when present."""
    registry = PluginRegistry()
    module = MagicMock()
    module.__all__ = ["FakeTool1"]
    module.FakeTool1 = FakeTool1

    registry._register_from_module(module)
    assert len(registry) == 1


def test_register_from_module_without_all():
    """_register_from_module should scan all attributes when __all__ missing."""
    registry = PluginRegistry()
    module = MagicMock()
    module.__all__ = None
    module.FakeTool1 = FakeTool1
    module.FakeTool2 = FakeTool2
    module.some_other = "not a tool"

    registry._register_from_module(module)
    assert len(registry) == 2


def test_register_from_module_ignores_base_tool():
    """_register_from_module should ignore BaseTool itself."""
    registry = PluginRegistry()
    module = MagicMock()
    module.__all__ = None
    module.BaseTool = BaseTool

    registry._register_from_module(module)
    assert len(registry) == 0


def test_register_from_module_ignores_non_tools():
    """_register_from_module should ignore non-BaseTool classes."""
    registry = PluginRegistry()
    module = MagicMock()
    module.__all__ = None
    module.NotATool = type("NotATool", (), {})

    registry._register_from_module(module)
    assert len(registry) == 0
