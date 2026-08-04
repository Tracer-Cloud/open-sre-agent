"""Tool-related type aliases."""

from __future__ import annotations

from enum import StrEnum


class ToolSurface(StrEnum):
    """Surfaces a tool can be exposed on.

    A ``StrEnum`` so members compare equal to and serialize as their plain
    string value (``ToolSurface.INVESTIGATION == "investigation"``), letting
    tool authors keep declaring bare strings while the framework stores members.
    """

    INVESTIGATION = "investigation"
    CHAT = "chat"
    ACTION = "action"


__all__ = ["ToolSurface"]
