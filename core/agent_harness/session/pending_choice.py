"""Structured multiple-choice question awaiting the user's menu selection.

The ``ask_user_choice`` action tool writes a :class:`PendingUserChoice` onto the
session and queues the ``/choose`` slash command; the shell's ``/choose`` handler
pops the object and renders it as an inline arrow-key menu with exclusive stdin.
The selected option label is then auto-submitted as the next user message, so the
agent receives the decision as structured conversation input — no prose scraping
and no "Reply with 1, 2, or 3" free-text parsing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PendingUserChoice:
    """A blocking decision the user makes via the shell selection menu."""

    title: str
    """Short question rendered as the menu title."""

    options: tuple[str, ...]
    """Option labels in display order; the selected label becomes the next user message."""


__all__ = ["PendingUserChoice"]
