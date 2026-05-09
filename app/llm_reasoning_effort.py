"""Helpers for session-scoped reasoning effort overrides."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal, cast

ReasoningEffortChoice = Literal["low", "medium", "high", "xhigh", "max"]

REASONING_EFFORT_OPTIONS: tuple[ReasoningEffortChoice, ...] = (
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

_RUNTIME_ENV_KEY = "OPENSRE_REASONING_EFFORT"
_RUNTIME_VALUES = frozenset({"low", "medium", "high", "xhigh"})

_reasoning_effort_session: ContextVar[str | None] = ContextVar(
    "opensre_reasoning_effort_session", default=None
)


def parse_reasoning_effort(value: str | None) -> ReasoningEffortChoice | None:
    """Return the normalized user-facing effort choice, if valid."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in REASONING_EFFORT_OPTIONS:
        return cast(ReasoningEffortChoice, normalized)
    return None


def runtime_reasoning_effort(choice: ReasoningEffortChoice | None) -> str | None:
    """Map a user-facing choice to the runtime value sent to model providers."""
    if choice is None:
        return None
    return "xhigh" if choice == "max" else choice


def display_reasoning_effort(choice: ReasoningEffortChoice | None) -> str:
    """Human-readable label for tables and slash-command output."""
    if choice is None:
        return "default"
    runtime = runtime_reasoning_effort(choice)
    if runtime and runtime != choice:
        return f"{choice} (runtime: {runtime})"
    return choice


def get_active_reasoning_effort() -> str | None:
    """Return the runtime reasoning-effort value for this logical context.

    Order: in-REPL session override (``apply_reasoning_effort``), then
    ``OPENSRE_REASONING_EFFORT`` in the process environment.
    """
    session = _reasoning_effort_session.get()
    if session is not None:
        return session if session in _RUNTIME_VALUES else None
    value = os.getenv(_RUNTIME_ENV_KEY, "").strip().lower()
    if value in _RUNTIME_VALUES:
        return value
    return None


def provider_supports_reasoning_effort(provider: str | None) -> bool:
    """Whether the current provider is wired to consume the REPL effort override."""
    return (provider or "").strip().lower() in {"openai", "codex"}


@contextmanager
def apply_reasoning_effort(choice: ReasoningEffortChoice | None) -> Iterator[None]:
    """Temporarily expose a session effort override to downstream model clients.

    ``choice is None`` means defer to shell/env defaults: do not clear
    ``OPENSRE_REASONING_EFFORT`` or mutate the process environment.

    Non-None choices use a :class:`contextvars.ContextVar` so concurrent REPL or
    CLI invocations on different threads/tasks do not race on ``os.environ``.
    """
    if choice is None:
        yield
        return
    runtime = runtime_reasoning_effort(choice)
    token = _reasoning_effort_session.set(runtime)
    try:
        yield
    finally:
        _reasoning_effort_session.reset(token)


__all__ = [
    "ReasoningEffortChoice",
    "REASONING_EFFORT_OPTIONS",
    "apply_reasoning_effort",
    "display_reasoning_effort",
    "get_active_reasoning_effort",
    "parse_reasoning_effort",
    "provider_supports_reasoning_effort",
    "runtime_reasoning_effort",
]
