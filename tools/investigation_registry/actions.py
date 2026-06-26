"""Registry of all available investigation actions."""

from tools.registered_tool import RegisteredTool
from tools.registry import get_registered_tools


def get_available_actions() -> list[RegisteredTool]:
    """Return investigation-surface tools discovered under ``tools/``."""
    return get_registered_tools("investigation")
