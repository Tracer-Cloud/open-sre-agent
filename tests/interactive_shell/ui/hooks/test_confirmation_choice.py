"""The confirmation hook renders a stacked Yes/No choice and delivers a pick.

The arrow marker follows ``confirm_selected``; ``y``/``n`` are the answers the
execution gate reads (allow vs cancel).
"""

from __future__ import annotations

import re

from surfaces.interactive_shell.ui.hooks import (
    confirmation_choice_overlay_ansi,
    confirmation_option_count,
    install_confirmation_key_bindings,
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


class _FakeState:
    def __init__(self) -> None:
        self.confirm_selected = 0
        self.awaiting = True
        self.delivered: list[str] = []

    def is_awaiting_confirmation(self) -> bool:
        return self.awaiting

    def deliver_confirmation(self, answer: str) -> None:
        self.delivered.append(answer)


def test_overlay_marks_the_selected_row_with_the_arrow() -> None:
    # Arrange / Act
    yes = _plain(confirmation_choice_overlay_ansi(0))
    no = _plain(confirmation_choice_overlay_ansi(1))

    # Assert: exactly one arrow, on the selected row.
    assert "❯ [a] Yes" in yes and "❯ [b] No" not in yes
    assert "❯ [b] No" in no and "❯ [a] Yes" not in no


def test_option_count_is_two() -> None:
    assert confirmation_option_count() == 2


def test_letter_keys_deliver_the_matching_answer() -> None:
    # Arrange
    state = _FakeState()
    redraws: list[bool] = []
    bindings = install_confirmation_key_bindings(state, lambda: redraws.append(True))
    by_key = {str(b.keys[0]): b.handler for b in bindings.bindings}

    # Act: pressing "n" cancels, "y" allows, regardless of the current arrow.
    by_key["n"](None)
    by_key["y"](None)

    # Assert
    assert state.delivered == ["n", "y"]


def test_arrow_keys_move_and_wrap_the_selection_then_enter_delivers_it() -> None:
    # Arrange
    state = _FakeState()
    redraws: list[bool] = []
    bindings = install_confirmation_key_bindings(state, lambda: redraws.append(True))
    by_key = {str(b.keys[0]): b.handler for b in bindings.bindings}

    # Act / Assert: Down moves Yes(0) -> No(1) and wraps No -> Yes.
    by_key["Keys.Down"](None)
    assert state.confirm_selected == 1
    by_key["Keys.Down"](None)
    assert state.confirm_selected == 0
    # Up wraps Yes(0) -> No(1); Enter then delivers the No answer.
    by_key["Keys.Up"](None)
    assert state.confirm_selected == 1
    by_key["Keys.ControlM"](None)
    assert state.delivered == ["n"]
    # Every movement repainted the prompt.
    assert redraws == [True, True, True]
