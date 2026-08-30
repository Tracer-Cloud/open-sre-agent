"""Ctrl+P hook to expand/collapse the pinned task-plan overlay.

A long plan collapses to a window around the current step; this toggles the
full checklist. The binding is gated on a plan being present, so when there is
no plan Ctrl+P falls through to its default (history) behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent


class PlanExpandState(Protocol):
    """The subset of ``ReplState`` this hook drives."""

    def toggle_plan_expanded(self) -> None:
        """Flip the collapsed/expanded state of the plan overlay."""


def install_plan_expand_key_bindings(
    state: PlanExpandState,
    has_plan: Callable[[], bool],
    redraw: Callable[[], None],
) -> KeyBindings:
    """Bind Ctrl+P to toggle the plan overlay while a plan is on screen."""
    kb = KeyBindings()
    plan_shown = Condition(has_plan)

    @kb.add("c-p", filter=plan_shown, eager=True)
    def _toggle(_event: KeyPressEvent) -> None:
        state.toggle_plan_expanded()
        redraw()

    return kb


__all__ = ["PlanExpandState", "install_plan_expand_key_bindings"]
