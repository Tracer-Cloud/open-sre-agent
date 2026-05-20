"""Runtime helpers for live routing turn-execution oracle tests."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pytest
from rich.console import Console

import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor as action_executor
import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions as agent_actions
import app.cli.interactive_shell.runtime.execution as runtime_execution
from app.cli.interactive_shell.routing.router import route_input
from app.cli.interactive_shell.routing.tests._oracle_normalize import (
    normalize_history_entry,
    normalize_response_text,
    oracle_action_matches,
)
from app.cli.interactive_shell.routing.tests.scenario_loader import ScenarioCase
from app.cli.interactive_shell.runtime.execution import execute_routed_turn
from app.cli.interactive_shell.runtime.session import ReplSession


@dataclass
class OracleRunResult:
    passed: bool
    details: dict[str, Any]


def fresh_session(*, with_prior_state: bool) -> ReplSession:
    session = ReplSession()
    if with_prior_state:
        session.last_state = {"root_cause": "disk full on orders-api"}
    return session


def match_actions(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> bool:
    if len(actual) != len(expected):
        return False
    return all(oracle_action_matches(item, expected[idx]) for idx, item in enumerate(actual))


def execution_expected_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in action.items() if key not in {"source", "target_surface", "content"}}
        for action in actions
    ]


def contains_any(haystack: str, needles: list[str]) -> bool:
    if not needles:
        return True
    normalized_needles = [normalize_response_text(needle) for needle in needles if needle.strip()]
    return any(needle in haystack for needle in normalized_needles)


def history_matches(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> bool:
    if len(actual) != len(expected):
        return False
    remaining = list(actual)
    for expected_item in expected:
        match_index = next(
            (
                idx
                for idx, candidate in enumerate(remaining)
                if oracle_action_matches(candidate, expected_item)
            ),
            -1,
        )
        if match_index < 0:
            return False
        remaining.pop(match_index)
    return True


def patch_execution_boundary(
    monkeypatch: pytest.MonkeyPatch,
    executed: list[dict[str, Any]],
) -> None:
    def _record_and_print(
        *,
        kind: str,
        content: str,
        session: ReplSession,
        console: Console,
        history_type: str,
    ) -> None:
        action: dict[str, Any] = {"kind": kind}
        if kind == "slash":
            parts = content.split()
            action["command"] = parts[0] if parts else ""
            action["args"] = parts[1:] if len(parts) > 1 else []
        elif kind == "synthetic_test":
            suite, _sep, scenario = content.partition(":")
            action["suite"] = suite
            action["scenario"] = scenario
        elif kind == "cli_command":
            action["payload"] = content
        elif kind == "sample_alert":
            action["template"] = content
        else:
            action["content"] = content
        executed.append(action)
        session.record(history_type, content, ok=True)
        if kind == "slash":
            console.print(f"ran {content}")
        else:
            console.print(f"executed {kind}: {content}")

    def _fake_dispatch(command: str, session: ReplSession, console: Console, **_: object) -> bool:
        _record_and_print(
            kind="slash",
            content=command.strip(),
            session=session,
            console=console,
            history_type="slash",
        )
        return True

    def _fake_sample(
        template_name: str,
        session: ReplSession,
        console: Console,
        **_: object,
    ) -> None:
        _record_and_print(
            kind="sample_alert",
            content=template_name.strip(),
            session=session,
            console=console,
            history_type="alert",
        )

    def _fake_synthetic(
        suite_name: str,
        session: ReplSession,
        console: Console,
        **_: object,
    ) -> None:
        _record_and_print(
            kind="synthetic_test",
            content=suite_name.strip(),
            session=session,
            console=console,
            history_type="synthetic_test",
        )

    def _fake_cli_command(
        args: str,
        session: ReplSession,
        console: Console,
        **_: object,
    ) -> bool:
        _record_and_print(
            kind="cli_command",
            content=args.strip(),
            session=session,
            console=console,
            history_type="cli_command",
        )
        return True

    def _fake_shell(
        command: str,
        session: ReplSession,
        console: Console,
        **_: object,
    ) -> None:
        _record_and_print(
            kind="shell",
            content=command.strip(),
            session=session,
            console=console,
            history_type="shell",
        )

    def _fake_investigation(
        alert_text: str,
        session: ReplSession,
        console: Console,
        **_: object,
    ) -> None:
        _record_and_print(
            kind="investigation",
            content=alert_text.strip(),
            session=session,
            console=console,
            history_type="alert",
        )

    monkeypatch.setattr(runtime_execution, "dispatch_slash", _fake_dispatch)
    monkeypatch.setattr(agent_actions, "dispatch_slash", _fake_dispatch)
    monkeypatch.setattr(agent_actions, "run_sample_alert", _fake_sample)
    monkeypatch.setattr(agent_actions, "run_synthetic_test", _fake_synthetic)
    monkeypatch.setattr(action_executor, "run_opensre_cli_command", _fake_cli_command)
    monkeypatch.setattr(action_executor, "run_shell_command", _fake_shell)
    monkeypatch.setattr(action_executor, "run_text_investigation", _fake_investigation)
    monkeypatch.setattr(agent_actions, "run_opensre_cli_command", _fake_cli_command)
    monkeypatch.setattr(agent_actions, "run_shell_command", _fake_shell)
    monkeypatch.setattr(agent_actions, "run_text_investigation", _fake_investigation)


def run_oracle_once(case: ScenarioCase, monkeypatch: pytest.MonkeyPatch) -> OracleRunResult:
    session = fresh_session(with_prior_state=case.scenario.session.has_prior_state)
    executed: list[dict[str, Any]] = []
    patch_execution_boundary(monkeypatch, executed)

    console_buffer = io.StringIO()
    console = Console(file=console_buffer, force_terminal=False, highlight=False, width=100)

    prompt = case.scenario.input.prompt
    decision = route_input(prompt, session)
    history_start = len(session.history)

    execute_routed_turn(
        prompt,
        session,
        console,
        on_exit=lambda: None,
        confirm_fn=lambda _prompt: "y",
        decision=decision,
    )

    answer = case.answer
    normalized_response = normalize_response_text(console_buffer.getvalue())
    history_delta = [normalize_history_entry(entry) for entry in session.history[history_start:]]

    executed_expected = execution_expected_actions([dict(action) for action in answer.executed_actions])
    history_expected = [dict(item) for item in answer.history_expected]

    executed_match = match_actions(executed, executed_expected)
    history_match = history_matches(history_delta, history_expected)
    must_contain_any = answer.response_contract.get("must_contain_any", [])
    must_not_contain = answer.response_contract.get("must_not_contain", [])
    any_match = contains_any(normalized_response, must_contain_any)
    forbidden = [
        token
        for token in must_not_contain
        if normalize_response_text(token) in normalized_response
    ]

    passed = True
    if decision.route_kind.value != answer.route.expected_kind:
        passed = False
    if answer.policy.should_execute:
        if not executed_match:
            passed = False
    else:
        if executed:
            passed = False
        if normalize_response_text("$ /") in normalized_response:
            passed = False
    if not any_match:
        passed = False
    if forbidden:
        passed = False
    if not history_match:
        passed = False

    return OracleRunResult(
        passed=passed,
        details={
            "id": case.scenario.id,
            "route_kind_actual": decision.route_kind.value,
            "route_kind_expected": answer.route.expected_kind,
            "executed_actions_actual": executed,
            "executed_actions_expected": executed_expected,
            "history_actual": history_delta,
            "history_expected": history_expected,
            "response_normalized": normalized_response,
            "response_contract": answer.response_contract,
            "forbidden_matches": forbidden,
        },
    )
