"""Alias catalog for deterministic CLI command resolution."""

from __future__ import annotations

_ALIASES_BY_TARGET: dict[str, tuple[str, ...]] = {
    "help": ("help", "?"),
    "exit": ("exit",),
    "quit": ("quit",),
    "clear": ("clear",),
    "reset": ("reset",),
    "status": ("status",),
    "trust": ("trust",),
    "onboard": ("onboard",),
    "remote": ("deploy", "remote"),
    "tests": ("tests",),
    "guardrails": ("guardrails",),
    "update": ("update",),
    "uninstall": ("uninstall",),
    "list": ("list",),
    "integrations": ("integrations", "integration", "int"),
    "mcp": ("mcp",),
    "agents": ("agents",),
    "doctor": ("doctor",),
    "welcome": ("welcome", "agent", "hi", "hey", "menu"),
}

_TARGETS_WITH_ARGS = frozenset({"integrations", "mcp"})


def _build_alias_map() -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for target, aliases in _ALIASES_BY_TARGET.items():
        slash_command = f"/{target}"
        for alias in aliases:
            alias_map[alias] = slash_command
    return alias_map


BARE_COMMAND_ALIAS_MAP = _build_alias_map()
BARE_COMMAND_ALIASES = frozenset(BARE_COMMAND_ALIAS_MAP.keys())
BARE_COMMAND_ALIASES_WITH_ARGS = frozenset(
    alias
    for target, aliases in _ALIASES_BY_TARGET.items()
    if target in _TARGETS_WITH_ARGS
    for alias in aliases
)
__all__ = [
    "BARE_COMMAND_ALIASES",
    "BARE_COMMAND_ALIASES_WITH_ARGS",
    "BARE_COMMAND_ALIAS_MAP",
]
