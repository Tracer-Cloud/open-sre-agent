from __future__ import annotations

import signal
import threading

import pytest

from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.llm.types import ToolCall
from core.tool.contracts import RegisteredTool, SideEffectLevel
from core.tool.execution import ToolExecutionHooks, ToolExecutionRequest
from surfaces.cli.ask import service
from surfaces.cli.ask.service import AskExitCode, AskSignal, AskStatus


def _turn(
    response: str = "answer",
    *,
    answered: bool = True,
    cancelled: bool = False,
) -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_cancelled" if cancelled else "answer",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
        ),
        assistant_response_text=response,
        llm_run=object() if answered else None,
    )


def _risky_request() -> ToolExecutionRequest:
    tool = RegisteredTool(
        name="shell_run",
        description="Run shell",
        input_schema={"type": "object", "properties": {}},
        source="shell",
        run=lambda: None,
        side_effect_level=SideEffectLevel.MUTATING,
    )
    return ToolExecutionRequest(
        tool_call=ToolCall(id="call-1", name=tool.name, input={}),
        tool=tool,
        arguments={},
        source=tool.source,
        resolved_integrations={},
    )


class _FakeSessionManager:
    def __init__(self) -> None:
        self.closed: list[tuple[object, bool]] = []

    def close(self, session: object, *, extract_memory: bool) -> None:
        self.closed.append((session, extract_memory))


class _FakeSession:
    def __init__(self) -> None:
        self.available_capabilities: dict[str, object] = {}


class _FakeAgentSession:
    session = _FakeSession()

    def __init__(self, _config: object) -> None:
        self.attached: object | None = None

    def startup(self) -> object:
        return type(
            "Startup",
            (),
            {"session": self.session, "prompts": object()},
        )()

    def attach_agent(self, agent: object) -> None:
        self.attached = agent

    def chat(self, _prompt: str) -> TurnResult:
        raise RuntimeError("turn failed")


class _FakeHeadlessBuild:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def agent(self, **_kwargs: object) -> object:
        return object()


def test_run_ask_returns_success(monkeypatch) -> None:
    monkeypatch.setattr(service, "_run_agent_turn", lambda _prompt, _hooks: _turn())

    outcome = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.SUCCESS
    assert outcome.response == "answer"
    assert outcome.exit_code is AskExitCode.SUCCESS


def test_agent_turn_closes_ephemeral_session_after_failure(monkeypatch) -> None:
    manager = _FakeSessionManager()
    _FakeAgentSession.session = _FakeSession()
    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _FakeAgentSession)
    monkeypatch.setattr(service, "DefaultHeadlessBuild", _FakeHeadlessBuild)
    monkeypatch.setattr(
        service,
        "DefaultToolProvider",
        lambda *_args, **_kwargs: object(),
    )

    with pytest.raises(RuntimeError, match="turn failed"):
        service._run_agent_turn("prompt", ToolExecutionHooks())

    assert manager.closed == [(_FakeAgentSession.session, False)]


def test_run_ask_reports_denial_before_agent_failure(monkeypatch) -> None:
    def deny_then_fail(_prompt: str, hooks) -> TurnResult:
        assert hooks.before_tool_call is not None
        decision = hooks.before_tool_call(_risky_request())
        assert decision is not None and decision.blocked
        raise RuntimeError("downstream detail")

    monkeypatch.setattr(service, "_run_agent_turn", deny_then_fail)

    outcome = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.APPROVAL_DENIED
    assert outcome.denied_tools == ("shell_run",)
    assert outcome.exit_code is AskExitCode.APPROVAL_DENIED
    assert "downstream detail" not in outcome.response


def test_run_ask_maps_incomplete_and_cancelled_turns(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda _prompt, _hooks: _turn("", answered=False),
    )
    incomplete = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda _prompt, _hooks: _turn("stopped", cancelled=True),
    )
    cancelled = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert incomplete.status is AskStatus.ERROR
    assert incomplete.exit_code is AskExitCode.ERROR
    assert cancelled.status is AskStatus.CANCELLED
    assert cancelled.exit_code is AskExitCode.SIGINT


@pytest.mark.parametrize(
    ("signum", "exit_code"),
    [(signal.SIGINT, AskExitCode.SIGINT), (signal.SIGTERM, AskExitCode.SIGTERM)],
)
def test_run_ask_maps_signals(monkeypatch, signum: int, exit_code: AskExitCode) -> None:
    def raise_signal(_prompt: str, _hooks) -> TurnResult:
        raise AskSignal(signum)

    monkeypatch.setattr(service, "_run_agent_turn", raise_signal)

    outcome = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.CANCELLED
    assert outcome.exit_code is exit_code


def test_signal_scope_requests_cancel_and_restores_handlers(monkeypatch) -> None:
    prior = {signal.SIGINT: object(), signal.SIGTERM: object()}
    installed: dict[signal.Signals, object] = {}
    restored: dict[signal.Signals, object] = {}

    monkeypatch.setattr(signal, "getsignal", lambda sig: prior[sig])

    def fake_signal(sig: signal.Signals, handler: object) -> None:
        if sig in installed:
            restored[sig] = handler
        else:
            installed[sig] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)
    cancel_event = threading.Event()

    with pytest.raises(AskSignal), service.ask_signal_scope(cancel_event):
        handler = installed[signal.SIGINT]
        assert callable(handler)
        handler(signal.SIGINT, None)

    assert cancel_event.is_set()
    assert restored == prior
