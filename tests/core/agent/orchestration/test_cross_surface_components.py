"""Component-level tests for modules on the shell ↔ gateway turn path."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult
from core.agent_harness.providers.default_providers import DefaultToolProvider
from core.agent_harness.session import InMemorySessionStorage, Session
from core.agent_harness.turns.orchestrator import run_turn
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


def test_run_turn_builds_turn_plan_for_action_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_turn resolves once and hands the action path a turn_plan carrying them."""
    resolved = {"github": {"configured": True}}
    monkeypatch.setattr(
        "core.agent_harness.turns.turn_plan.resolve_and_cache_integrations",
        lambda _session: resolved,
    )
    captured: list[Any] = []

    def execute_actions(
        _text: str, *, turn_plan: Any = None, **_kwargs: object
    ) -> ToolCallingTurnResult:
        captured.append(turn_plan)
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


def test_run_turn_passes_turn_plan_to_gather(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_turn hands the gather phase the turn_plan carrying resolved integrations (no re-resolve)."""
    resolved = {"github": {"configured": True}}
    monkeypatch.setattr(
        "core.agent_harness.turns.turn_plan.resolve_and_cache_integrations",
        lambda _session: resolved,
    )
    gather_calls: list[Any] = []

    def execute_actions(_text: str, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(0, 0, 0, False, False)

    def answer(_text: str, **_kwargs: object) -> object:
        return type("Run", (), {"response_text": "answered"})()

    def gather(_text: str, *, turn_plan: Any = None, **_kwargs: object) -> None:
        gather_calls.append(turn_plan.resolved_integrations if turn_plan is not None else None)
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

    assert gather_calls == [resolved]


def test_run_turn_passes_turn_plan_to_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The answer phase receives the same turn_plan (its snapshot grounds the prompt)."""
    resolved = {"github": {"configured": True}}
    monkeypatch.setattr(
        "core.agent_harness.turns.turn_plan.resolve_and_cache_integrations",
        lambda _session: resolved,
    )
    answer_plans: list[Any] = []

    def execute_actions(_text: str, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(0, 0, 0, False, False)

    def answer(_text: str, *, turn_plan: Any = None, **_kwargs: object) -> object:
        answer_plans.append(turn_plan)
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
        "why is it down?",
        session,
        execute_actions=execute_actions,
        answer=answer,
        gather=gather,
        accounting=_Accounting(),
    )

    assert answer_plans, "answer was never called"
    assert answer_plans[0] is not None
    assert answer_plans[0].snapshot.text == "why is it down?"
    assert answer_plans[0].resolved_integrations == resolved


def test_action_tools_uses_passed_resolved_integrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The turn's resolved dict is what tools are built from — no second resolve."""
    captured: list[dict[str, Any]] = []

    def _fake_build(_ctx: Any, *, resolved_integrations: dict[str, Any]) -> list[Any]:
        captured.append(resolved_integrations)
        return []

    monkeypatch.setattr(
        "core.agent_harness.providers.default_providers.get_action_tools_from_integrations_context",
        _fake_build,
    )
    provider = DefaultToolProvider(
        Session(storage=InMemorySessionStorage()), Console(force_terminal=False)
    )
    turn_resolved = {"github": {"configured": True}}

    provider.action_tools(confirm_fn=None, is_tty=False, resolved_integrations=turn_resolved)

    assert captured == [turn_resolved]


def test_action_tools_falls_back_to_session_resolve_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting the turn's dict keeps the prior behavior: resolve from the session."""
    captured: list[dict[str, Any]] = []
    session_resolved = {"slack": {"configured": True}}

    def _fake_build(_ctx: Any, *, resolved_integrations: dict[str, Any]) -> list[Any]:
        captured.append(resolved_integrations)
        return []

    monkeypatch.setattr(
        "core.agent_harness.providers.default_providers.get_action_tools_from_integrations_context",
        _fake_build,
    )
    monkeypatch.setattr(
        "core.agent_harness.integrations.resolution.resolve_and_cache_integrations",
        lambda _session: dict(session_resolved),
    )
    provider = DefaultToolProvider(
        Session(storage=InMemorySessionStorage()), Console(force_terminal=False)
    )

    provider.action_tools(confirm_fn=None, is_tty=False)

    assert captured == [session_resolved]
