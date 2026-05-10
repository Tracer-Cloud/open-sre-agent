"""Frozen value types shared by intent parsing and action planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PlannedAction:
    """A deterministic action inferred from a natural-language terminal request."""

    kind: Literal["llm_provider", "slash", "shell", "sample_alert", "synthetic_test", "cli_command"]
    content: str
    position: int


@dataclass(frozen=True)
class PromptClause:
    """A single clause from a compound natural-language prompt."""

    text: str
    position: int


__all__ = ["PlannedAction", "PromptClause"]
