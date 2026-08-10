"""Detached investigation launch protocol and context binding.

Tier-2 seam for chat investigations. The gateway binds a launcher
implementation; surfaces read it via contextvar.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "DetachedLaunchResult",
    "DetachedInvestigationLauncher",
    "bound_detached_launcher",
    "current_detached_launcher",
]


@dataclass(frozen=True)
class DetachedLaunchResult:
    """Result of launching a detached investigation."""

    investigation_id: str
    accepted: bool
    refusal_reason: str = ""  # non-empty only when accepted is False


class DetachedInvestigationLauncher(Protocol):
    """Protocol for launching detached investigations from chat."""

    def launch(
        self,
        *,
        alert_text: str,
        context_overrides: dict[str, Any] | None = None,
    ) -> DetachedLaunchResult:
        """Queue a detached investigation for the currently bound chat thread."""


_CURRENT_LAUNCHER: ContextVar[DetachedInvestigationLauncher | None] = ContextVar(
    "_CURRENT_LAUNCHER", default=None
)


@contextmanager
def bound_detached_launcher(launcher: DetachedInvestigationLauncher) -> Iterator[None]:
    """Bind a detached investigation launcher for the current context."""
    token = _CURRENT_LAUNCHER.set(launcher)
    try:
        yield
    finally:
        _CURRENT_LAUNCHER.reset(token)


def current_detached_launcher() -> DetachedInvestigationLauncher | None:
    """Return the bound launcher, or ``None`` when this turn runs locally.

    A sentinel rather than an exception on purpose. Callers wrap the launch in a
    ``try`` to fall back to the foreground pipeline, so raising "nothing is bound"
    is indistinguishable from the launch itself failing — and the fallback is the
    synchronous 277s run that detaching exists to avoid.
    """
    return _CURRENT_LAUNCHER.get()
