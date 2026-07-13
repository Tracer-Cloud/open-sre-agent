"""ShellOutputSink.render_error appends ``/model`` and ``/auth login`` hints on a credit-exhausted error."""

from __future__ import annotations

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
