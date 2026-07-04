"""Component-level tests for modules on the shell ↔ gateway turn path."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.agents.turn_orchestrator import run_turn
from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult
from core.agent_harness.providers.default_providers import DefaultToolProvider
from core.agent_harness.session import InMemorySessionStorage, Session
from gateway.turn_handler import build_gateway_turn_handler


def test_gateway_turn_handler_delegates_to_agent_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _spy(*args: Any, **kwargs: Any) -> ShellTurnResult:
        captured.append((args, kwargs))
        return ShellTurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
                response_text="gateway-ok",
            ),
            assistant_response_text="gateway-ok",
        )

    monkeypatch.setattr("gateway.turn_handler.dispatch_message_to_headless_agent", _spy)

    session = Session(storage=InMemorySessionStorage())
    sink = MagicMock()
    handler = build_gateway_turn_handler(console=Console(force_terminal=False))
    handler("hello gateway", session, sink, logging.getLogger("test.gateway.module"))

    assert len(captured) == 1
    args, kwargs = captured[0]
    assert args == ("hello gateway",)
    assert kwargs["session"] is session
    assert kwargs["output"] is sink
    assert kwargs["gather_enabled"] is True
    assert isinstance(kwargs["tools"], DefaultToolProvider)
    assert kwargs["tools"]._precomputed_action_tools is None
    sink.finalize.assert_called_once_with("gateway-ok")


def test_gateway_turn_handler_does_not_finalize_answered_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "gateway.turn_handler.dispatch_message_to_headless_agent",
        lambda *_args, **_kwargs: ShellTurnResult(
            final_intent="cli_agent_fallback",
            action_result=ToolCallingTurnResult(0, 0, 0, False, False),
            assistant_response_text="streamed answer",
            llm_run=object(),
        ),
    )

    session = Session(storage=InMemorySessionStorage())
    sink = MagicMock()
    handler = build_gateway_turn_handler(console=Console(force_terminal=False))
    handler("why", session, sink, logging.getLogger("test.gateway.module.answer"))

    sink.finalize.assert_not_called()


def test_run_turn_routes_unhandled_action_to_answer_callback() -> None:
    action = ToolCallingTurnResult(0, 0, 0, False, False)
    answer_calls: list[str] = []

    def execute_actions(_text: str, **_kwargs: object) -> ToolCallingTurnResult:
        return action

    def answer(text: str, **_kwargs: object) -> object:
        answer_calls.append(text)
        return type("Run", (), {"response_text": "answered"})()

    def gather(_text: str, **_kwargs: object) -> None:
        return None

    class _Accounting:
        def record_action_result(self, _result: ToolCallingTurnResult) -> None:
            return None

        def finalize(self, result: ShellTurnResult) -> ShellTurnResult:
            return result

    session = Session(storage=InMemorySessionStorage())
    result = run_turn(
        "question?",
        session,
        execute_actions=execute_actions,
        answer=answer,
        gather=gather,
        accounting=_Accounting(),
    )

    assert answer_calls == ["question?"]
    assert result.final_intent == "cli_agent_fallback"
    assert result.answered is True


def test_run_turn_populates_resolved_integrations_on_turn_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_turn resolves integrations once and hands them to the action path on turn_snapshot."""
    resolved = {"github": {"configured": True}}
    monkeypatch.setattr(
        "core.agent_harness.agents.turn_orchestrator.resolve_and_cache_integrations",
        lambda _session: resolved,
    )
    captured: list[Any] = []

    def execute_actions(
        _text: str, *, turn_snapshot: Any = None, **_kwargs: object
    ) -> ToolCallingTurnResult:
        captured.append(turn_snapshot)
        return ToolCallingTurnResult(0, 0, 0, False, False)

    def answer(_text: str, **_kwargs: object) -> object:
        return type("Run", (), {"response_text": "answered"})()

    def gather(_text: str, **_kwargs: object) -> None:
        return None

    class _Accounting:
        def record_action_result(self, _result: ToolCallingTurnResult) -> None:
            return None

        def finalize(self, result: ShellTurnResult) -> ShellTurnResult:
            return result

    session = Session(storage=InMemorySessionStorage())
    run_turn(
        "hi",
        session,
        execute_actions=execute_actions,
        answer=answer,
        gather=gather,
        accounting=_Accounting(),
    )

    assert captured, "execute_actions was never called"
    assert captured[0].resolved_integrations == resolved
