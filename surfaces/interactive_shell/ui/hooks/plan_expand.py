"""Alt/Option+P (and Ctrl+P) expand/collapse the pinned task-plan overlay.

A long plan collapses to a window around the current step; this toggles the
full checklist. Bindings are gated on a plan being present, so when there is
no plan the keys fall through to their defaults (history for Ctrl+P).
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
    """Bind Alt/Option+P and Ctrl+P to toggle the plan overlay while a plan is on screen."""
    kb = KeyBindings()
    plan_shown = Condition(has_plan)

    def _toggle(_event: KeyPressEvent) -> None:
        state.toggle_plan_expanded()
        redraw()

    # Alt/Option+P (ESC p in most terminals); Ctrl+P kept as a fallback.
    @kb.add("escape", "p", filter=plan_shown, eager=True)
    def _toggle_alt_p(event: KeyPressEvent) -> None:
        _toggle(event)

    @kb.add("c-p", filter=plan_shown, eager=True)
    def _toggle_ctrl_p(event: KeyPressEvent) -> None:
        _toggle(event)

    return kb


__all__ = ["PlanExpandState", "install_plan_expand_key_bindings"]
