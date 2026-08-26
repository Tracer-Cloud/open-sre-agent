"""Surface-neutral contracts for blocking human hand-offs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class UserChoiceOption:
    """One user-facing answer and its concise trade-off description."""

    label: str
    description: str


@dataclass(frozen=True, slots=True)
class UserChoiceRequest:
    """One blocking multiple-choice question for a human operator."""

    id: str
    header: str
    question: str
    options: tuple[UserChoiceOption, ...]


class HumanInteractionPort(Protocol):
    """Surface-owned blocking human interaction."""

    def choose(self, request: UserChoiceRequest) -> str | None:
        """Wait for a selected label, custom answer, or cancellation."""


__all__ = ["HumanInteractionPort", "UserChoiceOption", "UserChoiceRequest"]
