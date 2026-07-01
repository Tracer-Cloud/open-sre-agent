"""Pure input-action decisions for the interactive shell controller."""

from __future__ import annotations

import pytest

from surfaces.interactive_shell.runtime.input import (
    InputCancelled,
    InputClosed,
    InputEvent,
    InputSubmitted,
)
from surfaces.interactive_shell.runtime.input.actions import (
    QUEUE_DURING_CONFIRMATION_WARNING,
    CancelTurn,
    CloseShell,
    DeliverConfirmation,
    IgnoreInput,
    ShellInputSnapshot,
    SubmitTurn,
    decide_input_action,
)


def _decide(
    event: InputEvent,
    *,
    exit_requested: bool = False,
    dispatch_running: bool = False,
    awaiting_confirmation: bool = False,
    needs_exclusive_stdin: bool = False,
) -> object:
    return decide_input_action(
        event,
        ShellInputSnapshot(
            exit_requested=exit_requested,
            dispatch_running=dispatch_running,
            awaiting_confirmation=awaiting_confirmation,
        ),
        needs_exclusive_stdin=lambda _text: needs_exclusive_stdin,
    )


def test_decide_closes_on_input_closed() -> None:
    assert _decide(InputClosed()) == CloseShell()


def test_decide_cancels_on_input_cancelled() -> None:
    assert _decide(InputCancelled()) == CancelTurn()


@pytest.mark.parametrize("text", ["", "   "])
def test_decide_ignores_empty_or_blank_submissions(text: str) -> None:
    assert _decide(InputSubmitted(text)) == IgnoreInput()


def test_decide_ignores_submitted_input_after_exit_requested() -> None:
    assert _decide(InputSubmitted("/status"), exit_requested=True) == IgnoreInput()


def test_decide_cancels_when_cancel_request_is_typed_during_dispatch() -> None:
    assert _decide(InputSubmitted(" /cancel "), dispatch_running=True) == CancelTurn(
        submitted_text="/cancel"
    )


def test_decide_delivers_stripped_confirmation_answer() -> None:
    assert _decide(
        InputSubmitted(" yes "),
        awaiting_confirmation=True,
    ) == DeliverConfirmation(text="yes")


def test_decide_submits_non_confirmation_input_while_confirmation_is_pending() -> None:
    assert _decide(
        InputSubmitted("run /status"),
        awaiting_confirmation=True,
    ) == SubmitTurn(
        text="run /status",
        warning=QUEUE_DURING_CONFIRMATION_WARNING,
    )


def test_decide_submits_normal_turn_without_wait_by_default() -> None:
    assert _decide(InputSubmitted("  show status  ")) == SubmitTurn(text="show status")


def test_decide_submits_normal_turn_with_exclusive_stdin_wait() -> None:
    assert _decide(InputSubmitted("/integrations"), needs_exclusive_stdin=True) == SubmitTurn(
        text="/integrations",
        wait_until_idle=True,
    )
