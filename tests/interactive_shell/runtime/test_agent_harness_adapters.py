"""ShellOutputSink.render_error appends ``/model`` and ``/auth login`` hints on a credit-exhausted error."""

from __future__ import annotations

import io
import re

from rich.console import Console

from core.agent_harness.session.pending_offer import ensure_canonical_investigation_closer
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


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def test_finish_streamed_response_paints_canonical_not_dual_menu() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system=None, width=100, highlight=False)
    sink = ShellOutputSink(console)
    dual = (
        "Grafana unreachable; kube refused.\n\n"
        "**Want me to:**\n"
        "1. run a full investigation once you paste the alert, or\n"
        "2. walk you through `/integrations setup grafana`?"
    )
    streamed = sink.stream(
        label="assistant",
        chunks=[dual],
        defer_want_me_to_closer=True,
    )
    mid = _strip_ansi(buf.getvalue())
    assert "Grafana unreachable" in mid
    assert "/integrations setup grafana" not in mid

    canonical = ensure_canonical_investigation_closer(streamed)
    sink.finish_streamed_response(canonical)
    final = _strip_ansi(buf.getvalue())
    assert "run a full investigation" in final
    assert "/integrations" not in final


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


def test_response_header_opens_without_a_leading_blank() -> None:
    """The sink used to print a blank before ``∴``; that padded turns vs Droid.

    ``_show_response`` used to own that spacer in the shared turn engine.
    Chat sinks never wanted it; keeping a blank in the shell sink only was
    the remaining gap between user row and reply marker.
    """
    # Arrange
    console = _RecordingConsole()

    # Act
    ShellOutputSink(console).render_response_header("assistant")  # type: ignore[arg-type]

    # Assert: marker on the first painted line — no spacer row above.
    assert console.lines
    assert "∴" in console.lines[0]
    assert console.lines[0] != ""
