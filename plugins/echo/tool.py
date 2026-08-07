"""
Echo plugin - returns the input as-is.
Used for testing the plugin system.
"""

from typing import Any

from core.base_tool import BaseTool


class EchoTool(BaseTool):
    """Simple echo tool for testing."""

    name = "echo"
    description = "Returns the input message as-is"
    version = "1.0.0"

    async def execute(self, message: str = "", **kwargs) -> dict[str, Any]:
        """Echo the message back."""
        return {
            "status": "success",
            "message": message,
            "echo": message,
        }

    def _get_parameters(self) -> dict[str, Any]:
        """Get parameter schema."""
        return {
            "message": {
                "type": "string",
                "description": "Message to echo back",
                "required": True,
            }
        }
