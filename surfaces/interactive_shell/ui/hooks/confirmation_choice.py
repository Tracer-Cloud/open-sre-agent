"""Arrow-navigable Yes/No hook for the execution-confirmation gate.

Renders the pending confirmation as a stacked ``[a] Yes`` / ``[b] No`` choice
driven with ↑/↓ and Enter (or the ``a``/``b``/``y``/``n`` letters) instead of a
typed ``Y/n`` answer. The free-text box is hidden while a confirmation is
active (see ``typing_box_hidden``), so the choice cannot be confused with a
message. Selecting delivers the answer straight to the parked turn worker.
"""

from __future__ import annotations

from typing import Protocol

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent

from infrastructure.terminal import theme as ui_theme

# (answer delivered to the gate, visible label). The gate treats "", "y", "yes"
# as allow and everything else as cancel, so "y"/"n" map cleanly to Yes/No.
_CONFIRM_OPTIONS: tuple[tuple[str, str], ...] = (("y", "Yes"), ("n", "No"))


class ConfirmChoiceState(Protocol):
    """The subset of ``ReplState`` this hook drives."""

    confirm_selected: int

    def is_awaiting_confirmation(self) -> bool:
        """True while a confirmation is pending and owns the keyboard."""

    def deliver_confirmation(self, answer: str) -> None:
        """Hand ``answer`` to the parked turn worker and wake it."""


def confirmation_option_count() -> int:
    """Number of selectable rows, so callers can clamp ``confirm_selected``."""
    return len(_CONFIRM_OPTIONS)


def confirmation_choice_overlay_ansi(selected: int) -> str:
    """Stacked ``❯ [a] Yes`` / ``  [b] No`` rows, accenting the selected one."""
    index = selected % len(_CONFIRM_OPTIONS)
    rows: list[str] = []
    for position, (_answer, label) in enumerate(_CONFIRM_OPTIONS):
        tag = chr(ord("a") + position)
        chosen = position == index
        marker = "❯" if chosen else " "
        style = ui_theme.PROMPT_ACCENT_ANSI if chosen else ui_theme.DIM_COUNTER_ANSI
        rows.append(f"{style} {marker} [{tag}] {label}{ui_theme.ANSI_RESET}")
    return "\n".join(rows)


def install_confirmation_key_bindings(state: ConfirmChoiceState, redraw: object) -> KeyBindings:
    """Bindings active only while awaiting confirmation: ↑/↓ move, Enter/letters pick.

    ``redraw`` invalidates the prompt so a selection change repaints at once.
    """
    kb = KeyBindings()
    awaiting = Condition(state.is_awaiting_confirmation)
    count = len(_CONFIRM_OPTIONS)

    def _move(delta: int) -> None:
        state.confirm_selected = (state.confirm_selected + delta) % count
        if callable(redraw):
            redraw()

    @kb.add("up", filter=awaiting, eager=True)
    def _on_up(_event: KeyPressEvent) -> None:
        _move(-1)

    @kb.add("down", filter=awaiting, eager=True)
    def _on_down(_event: KeyPressEvent) -> None:
        _move(1)

    @kb.add("c-m", filter=awaiting, eager=True)
    def _on_enter(_event: KeyPressEvent) -> None:
        answer, _label = _CONFIRM_OPTIONS[state.confirm_selected % count]
        state.deliver_confirmation(answer)

    # Letter shortcuts: the a/b row tags and the y/n answer keys both pick a row.
    for position, (answer, _label) in enumerate(_CONFIRM_OPTIONS):
        for key in {chr(ord("a") + position), answer}:

            @kb.add(key, filter=awaiting, eager=True)
            def _on_letter(_event: KeyPressEvent, _answer: str = answer) -> None:
                state.deliver_confirmation(_answer)

    return kb


__all__ = [
    "ConfirmChoiceState",
    "confirmation_choice_overlay_ansi",
    "confirmation_option_count",
    "install_confirmation_key_bindings",
]
