"""Characterization: ``dispatch_chat_turn`` is the common chat host API."""

from __future__ import annotations

from typing import Any

from core.agent_harness.turns.chat_api import ChatTurnBindings, dispatch_chat_turn
from core.agent_harness.turns.headless_dispatch import HeadlessAgent, NullToolProvider
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


def _stub_turn_result(*, text: str = "ok") -> TurnResult:
    return TurnResult(
        final_intent="answered",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text=text,
        llm_run=object(),
    )


def test_dispatch_chat_turn_forwards_bindings_to_run_turn(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}
    expected = _stub_turn_result(text="ok")

    def _fake_run_turn(
        message: str,
        session: Any,
        *,
        execute_actions: Any,
        answer: Any,
        gather: Any,
        accounting: Any,
        confirm_fn: Any = None,
        is_tty: bool | None = None,
        surface: str = "interactive_shell",
        output: Any = None,
    ) -> TurnResult:
        seen.update(
            {
                "message": message,
                "session": session,
                "execute_actions": execute_actions,
                "answer": answer,
                "gather": gather,
                "accounting": accounting,
                "confirm_fn": confirm_fn,
                "is_tty": is_tty,
                "surface": surface,
                "output": output,
            }
        )
        return expected

    monkeypatch.setattr("core.agent_harness.turns.chat_api.run_turn", _fake_run_turn)

    session = object()
    execute = object()
    answer = object()
    gather = object()
    accounting = object()
    confirm = object()
    output = object()
    bindings = ChatTurnBindings(
        execute_actions=execute,  # type: ignore[arg-type]
        answer=answer,  # type: ignore[arg-type]
        gather=gather,  # type: ignore[arg-type]
        accounting=accounting,  # type: ignore[arg-type]
        confirm_fn=confirm,  # type: ignore[arg-type]
        is_tty=False,
        surface="gateway",
        output=output,  # type: ignore[arg-type]
    )

    result = dispatch_chat_turn("hello", session, bindings)  # type: ignore[arg-type]

    assert result is expected
    assert seen == {
        "message": "hello",
        "session": session,
        "execute_actions": execute,
        "answer": answer,
        "gather": gather,
        "accounting": accounting,
        "confirm_fn": confirm,
        "is_tty": False,
        "surface": "gateway",
        "output": output,
    }


def test_headless_agent_dispatch_uses_dispatch_chat_turn(monkeypatch: Any) -> None:
    expected = _stub_turn_result(text="via-api")
    captured: list[Any] = []

    def _fake_dispatch(message: str, session: Any, bindings: ChatTurnBindings) -> TurnResult:
        captured.append((message, session, bindings))
        return expected

    monkeypatch.setattr(
        "core.agent_harness.turns.headless_dispatch.dispatch_chat_turn",
        _fake_dispatch,
    )

    agent = HeadlessAgent(tools=NullToolProvider())
    result = agent.dispatch("ping")

    assert result is expected
    assert len(captured) == 1
    message, session, bindings = captured[0]
    assert message == "ping"
    assert session is agent._store
    assert isinstance(bindings, ChatTurnBindings)
    assert bindings.is_tty is agent._is_tty


def test_only_chat_api_module_defines_dispatch_chat_turn() -> None:
    """Forbid a second host-facing ``dispatch_chat_turn`` definer."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    definers: list[str] = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts or ".venv" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "dispatch_chat_turn":
                definers.append(str(path.relative_to(root)))

    assert definers == ["core/agent_harness/turns/chat_api.py"], definers
