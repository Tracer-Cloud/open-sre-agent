"""The turn-error render adds ``/model`` and ``/auth login`` recovery hints on a credit-exhausted error."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from core.llm.shared.llm_retry import (
    LLMCreditExhaustedError,
    OpenSRECreditsExhaustedError,
)
from surfaces.interactive_shell.runtime import agent_presentation as ap
from surfaces.interactive_shell.runtime.agent_presentation import (
    AgentEvent,
    AgentPresentationState,
    ConsoleAgentEventSink,
    _render_agent_presentation_transition,
)


class _RecordingConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, text: str = "") -> None:
        self.lines.append(text)


def test_turn_complete_chimes_only_for_a_long_turn(monkeypatch) -> None:
    # A walk-away-length turn chimes on completion; a quick reply stays silent so
    # the shell does not beep on every short interactive turn.
    calls: list[object] = []
    monkeypatch.setattr(ap, "play_notification", lambda event: calls.append(event))
    clock = {"now": 1000.0}
    monkeypatch.setattr(ap.time, "monotonic", lambda: clock["now"])
    sink = ConsoleAgentEventSink(session=MagicMock(), spinner=MagicMock(), console=MagicMock())

    sink._turn_started_at = 1000.0
    clock["now"] = 1000.0 + ap.SOUND_MIN_TURN_SECONDS - 1
    sink._chime_if_long_turn()
    assert calls == []  # under the threshold: silent

    sink._turn_started_at = 1000.0
    clock["now"] = 1000.0 + ap.SOUND_MIN_TURN_SECONDS + 1
    sink._chime_if_long_turn()
    assert calls == [ap.NotifyEvent.TURN_COMPLETE]


def _render_turn_error(error: Exception) -> str:
    console = _RecordingConsole()
    asyncio.run(
        _render_agent_presentation_transition(
            previous=AgentPresentationState(),
            current=AgentPresentationState(),
            event=AgentEvent(type="turn_error", error=error),
            console=console,  # type: ignore[arg-type]
            spinner=MagicMock(),
        )
    )
    return "\n".join(console.lines)


def test_credit_exhausted_turn_error_shows_model_hint() -> None:
    output = _render_turn_error(LLMCreditExhaustedError("Anthropic credit exhausted"))
    assert "/model" in output


def test_credit_exhausted_turn_error_shows_auth_login_hint() -> None:
    output = _render_turn_error(LLMCreditExhaustedError("Anthropic credit exhausted"))
    assert "/auth login" in output


def test_opensre_credit_exhaustion_shows_checkout_instead_of_provider_hints() -> None:
    upgrade_url = "https://app.opensre.dev/usage"
    output = _render_turn_error(
        OpenSRECreditsExhaustedError(
            "OpenSRE credits exhausted",
            upgrade_url=upgrade_url,
        )
    )
    assert upgrade_url in output
    assert "/model" not in output
    assert "/auth login" not in output


def test_other_turn_error_has_no_model_hint() -> None:
    output = _render_turn_error(RuntimeError("something else broke"))
    assert "/model" not in output
    assert "/auth login" not in output
