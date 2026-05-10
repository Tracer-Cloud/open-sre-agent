"""Slash command handlers for the REPL (compatibility shim for the modular registry)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from importlib import import_module
from types import ModuleType
from typing import Any, cast

from app.cli.interactive_shell.command_registry.types import SlashCommand


def _registry_module() -> ModuleType:
    return import_module("app.cli.interactive_shell.command_registry")


class _SlashCommandProxy(Mapping[str, SlashCommand]):
    """Mapping facade that always reads from the current command registry module."""

    def _commands(self) -> Mapping[str, SlashCommand]:
        return cast(Mapping[str, SlashCommand], _registry_module().SLASH_COMMANDS)

    def __getitem__(self, key: str) -> SlashCommand:
        return self._commands()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._commands())

    def __len__(self) -> int:
        return len(self._commands())


SLASH_COMMANDS: Mapping[str, SlashCommand] = _SlashCommandProxy()


def dispatch_slash(*args: Any, **kwargs: Any) -> bool:
    return cast(bool, _registry_module().dispatch_slash(*args, **kwargs))


def switch_llm_provider(*args: Any, **kwargs: Any) -> bool:
    return cast(bool, _registry_module().switch_llm_provider(*args, **kwargs))


def switch_toolcall_model(*args: Any, **kwargs: Any) -> bool:
    return cast(bool, _registry_module().switch_toolcall_model(*args, **kwargs))


__all__ = [
    "SLASH_COMMANDS",
    "SlashCommand",
    "dispatch_slash",
    "switch_llm_provider",
    "switch_toolcall_model",
]
