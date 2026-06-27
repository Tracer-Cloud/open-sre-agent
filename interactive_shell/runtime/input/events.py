"""Explicit input events consumed by the interactive shell loop."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InputSubmitted:
    text: str


@dataclass(frozen=True)
class InputCancelled:
    pass


@dataclass(frozen=True)
class InputClosed:
    pass


InputEvent = InputSubmitted | InputCancelled | InputClosed


__all__ = ["InputCancelled", "InputClosed", "InputEvent", "InputSubmitted"]
