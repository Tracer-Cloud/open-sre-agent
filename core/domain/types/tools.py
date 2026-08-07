"""Tool-related type aliases."""

from __future__ import annotations

from enum import StrEnum


class ToolSurface(StrEnum):
    """Closed set of surfaces a registered tool can be exposed on."""

    INVESTIGATION = "investigation"
    CHAT = "chat"
    ACTION = "action"
