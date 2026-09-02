"""ShellOutputSink.render_error appends ``/model`` and ``/auth login`` hints on a credit-exhausted error."""

from __future__ import annotations

import io

from rich.console import Console

from core.llm.shared.llm_retry import CREDIT_EXHAUSTED_MARKER
from surfaces.interactive_shell.runtime.agent_harness_adapters import ShellOutputSink


class _RecordingConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str = "") -> None:
        self.lines.append(str(message))


def _render_error(message: str) -> str:
    console = _RecordingConsole()
    ShellOutputSink(console).render_error(message)  # type: ignore[arg-type]
    return "\n".join(console.lines)


def test_render_error_shows_model_hint_on_credit_exhaustion() -> None:
    output = _render_error(f"Anthropic {CREDIT_EXHAUSTED_MARKER}. Original error: 400")
    assert "/model" in output


def test_render_error_shows_auth_login_hint_on_credit_exhaustion() -> None:
    output = _render_error(f"Anthropic {CREDIT_EXHAUSTED_MARKER}. Original error: 400")
    assert "/auth login" in output


def test_render_error_no_hint_for_generic_error() -> None:
    output = _render_error("some other failure")
    assert "/model" not in output
    assert "/auth login" not in output


def test_finalize_does_not_reprint_an_answer_the_console_already_showed() -> None:
    """The REPL renders as it goes, so ``finalize`` must print nothing.

    The turn host calls ``finalize`` whenever a turn was not ``answered``, and
    ``answered`` means "the conversational LLM produced a run" — not "the user
    has seen the answer". A turn answered from the action phase (a skill, a tool
    chain) leaves it False while the console has already painted the reply.

    A chat transport needs the call: it holds one placeholder message and this
    is how that message gets its final text. A terminal has no placeholder, so
    printing here rendered every action-phase answer a second time, unformatted
    — the raw markdown showed up under the rendered one.
    """
    # Arrange
    buffer = io.StringIO()
    sink = ShellOutputSink(Console(file=buffer, force_terminal=False, width=100))

    # Act
    sink.finalize("**GitHub Actions** for `Tracer-Cloud/opensre`: 3% hard failure rate")

    # Assert
    assert buffer.getvalue() == ""


def test_finalize_satisfies_the_host_contract_while_staying_silent() -> None:
    """Silent, but still a ``TurnOutput`` — the host must be able to call it."""
    # Arrange
    from infrastructure.turn_host.turn_output import TurnOutput

    sink = ShellOutputSink(Console(file=io.StringIO(), force_terminal=False))

    # Assert
    assert isinstance(sink, TurnOutput)


def test_response_header_opens_with_a_blank_line() -> None:
    """The sink owns this spacing: the turn engine no longer emits it.

    ``_show_response`` used to print a blank line before the header. That was
    terminal layout living in the shared turn engine — chat sinks route
    ``print`` to a placeholder status and never wanted it. Moving it here kept
    the REPL looking the same; without a test, deleting it stays green.
    """
    # Arrange
    console = _RecordingConsole()

    # Act
    ShellOutputSink(console).render_response_header("assistant")  # type: ignore[arg-type]

    # Assert: blank line first, then the Ω marker.
    assert console.lines[0] == ""
    assert "Ω" in console.lines[1]
