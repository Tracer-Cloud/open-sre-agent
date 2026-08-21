"""Subprocess presenter the default agent gives shell tools.

Streaming shell/CLI output needs a presenter that lives in ``tools`` (its
process helpers) and is therefore invisible to ``core.agent_harness``.
Registering it here lets the default agent execute shell tools instead of
refusing them, without core importing tools or gateway.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent_harness.ports import SubprocessPresenterFactory

_subprocess_presenter_factory: SubprocessPresenterFactory | None = None


def set_subprocess_presenter_factory(factory: SubprocessPresenterFactory | None) -> None:
    """Register (or clear) the presenter the default agent gives shell tools."""
    global _subprocess_presenter_factory
    _subprocess_presenter_factory = factory


def get_subprocess_presenter_factory() -> SubprocessPresenterFactory | None:
    """Return the registered presenter factory, or ``None`` before boot."""
    return _subprocess_presenter_factory


def reset() -> None:
    """Restore the presenter to its unset default (tests)."""
    set_subprocess_presenter_factory(None)


__all__ = ["get_subprocess_presenter_factory", "set_subprocess_presenter_factory"]
