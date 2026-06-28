"""Frozen value types used by the turn-test harness oracle."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from tools.interactive_shell.registry import ToolKind

ActionSource = Literal["deterministic", "llm"]
TargetSurface = Literal["slash", "terminal", "investigation", "implementation"]


def default_target_surface(kind: ToolKind) -> TargetSurface | None:
    """Return the canonical execution surface for a given action kind."""
    if kind == "assistant_handoff":
        return None
    if kind in {"slash", "llm_provider", "task_cancel"}:
        return "slash"
    if kind in {"shell", "cli_command"}:
        return "terminal"
    if kind == "implementation":
        return "implementation"
    return "investigation"


@dataclass(frozen=True)
class PlannedAction:
    """A structured action inferred from a natural-language terminal request."""

    kind: ToolKind
    content: str
    position: int
    source: ActionSource = "deterministic"
    confidence: float = 1.0
    rationale: str | None = None
    target_surface: TargetSurface | None = None
    args: dict[str, object] = field(default_factory=dict)


__all__ = [
    "ActionSource",
    "PlannedAction",
    "TargetSurface",
    "default_target_surface",
]
