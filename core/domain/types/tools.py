"""Tool-related type aliases."""

from __future__ import annotations

from enum import StrEnum


class ToolSurface(StrEnum):
    """Runtime surfaces a registered tool may be exposed on."""

    INVESTIGATION = "investigation"
    CHAT = "chat"
    ACTION = "action"
