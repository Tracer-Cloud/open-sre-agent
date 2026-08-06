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
