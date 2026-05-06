"""Slash-command type definitions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console

from app.cli.interactive_shell.session import ReplSession


@dataclass(frozen=True)
class SlashCommand:
    name: str
    help_text: str
    handler: Callable[[ReplSession, Console, list[str]], bool]


__all__ = ["SlashCommand"]
