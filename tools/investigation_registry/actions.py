"""Registry of all available investigation actions."""

from core.domain.types.tools import ToolSurface
from core.tool_framework.registered_tool import RegisteredTool
from tools.registry import get_registered_tools


def get_available_actions() -> list[RegisteredTool]:
    """Return investigation-surface tools discovered under ``tools/``."""
    return get_registered_tools(ToolSurface.INVESTIGATION)
