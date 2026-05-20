"""Frozen value types shared by intent parsing and action planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ActionKind = Literal[
    "llm_provider",
    "slash",
    "shell",
    "sample_alert",
    "investigation",
    "synthetic_test",
    "task_cancel",
    "cli_command",
    "implementation",
    "assistant_handoff",
]
ActionSource = Literal["deterministic", "llm"]
TargetSurface = Literal["slash", "terminal", "investigation", "implementation"]


def default_target_surface(kind: ActionKind) -> TargetSurface | None:
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

    kind: ActionKind
    content: str
    position: int
    source: ActionSource = "deterministic"
    confidence: float = 1.0
    rationale: str | None = None
    target_surface: TargetSurface | None = None
    args: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptClause:
    """A single clause from a compound natural-language prompt."""

    text: str
    position: int


__all__ = [
    "ActionKind",
    "ActionSource",
    "PlannedAction",
    "PromptClause",
    "TargetSurface",
    "default_target_surface",
]
