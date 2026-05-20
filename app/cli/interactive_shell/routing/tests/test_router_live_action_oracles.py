"""Strict live oracle tests for router -> planner -> executor behavior."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

import pytest
from pydantic import ValidationError
from rich.console import Console

import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor as action_executor
import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions as agent_actions
import app.cli.interactive_shell.runtime.execution as runtime_execution
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    _call_llm,
    _sanitise_text,
    plan_actions_with_llm,
)
from app.cli.interactive_shell.routing.router import RouteKind, route_input
from app.cli.interactive_shell.routing.tests._dataset_schema import load_yaml_dataset
from app.cli.interactive_shell.routing.tests._oracle_normalize import (
    normalize_history_entry,
    normalize_planned_action,
    normalize_response_text,
    oracle_action_matches,
)
from app.cli.interactive_shell.runtime.execution import execute_routed_turn
from app.cli.interactive_shell.runtime.session import ReplSession
from app.config import LLMSettings, get_configured_llm_provider, get_llm_provider_api_key_env

pytestmark = [pytest.mark.integration, pytest.mark.live_llm]


class OracleContext(TypedDict):
    with_prior_state: bool


class OracleExpected(TypedDict):
    route_kind: str
    should_execute: bool
    has_unhandled_clause: bool
    actions: list[dict[str, Any]]
    response_contract: dict[str, list[str]]
    history: list[dict[str, Any]]


class OracleCase(TypedDict):
    id: str
    tier: str
    runs: int
    input: str
    context: OracleContext
    expected: OracleExpected


@dataclass
class OracleRunResult:
    passed: bool
    details: dict[str, Any]


def _load_oracle_cases(*, include_full: bool) -> list[OracleCase]:
    rows = load_yaml_dataset("router_live_action_oracles.yml")
    cases: list[OracleCase] = []
    for row in rows:
        row_id = str(row.get("id", "")).strip()
        tier = str(row.get("tier", "critical")).strip() or "critical"
        if tier == "full" and not include_full:
            continue
        runs_raw = row.get("runs", 1)
        runs = int(runs_raw) if isinstance(runs_raw, int | str) else 1
        context_raw = row.get("context", {})
        expected_raw = row.get("expected", {})
        if not isinstance(context_raw, dict) or not isinstance(expected_raw, dict):
            msg = f"Invalid oracle case {row_id!r}: context and expected must be mappings."
            raise ValueError(msg)
        cases.append(
            OracleCase(
                id=row_id,
                tier=tier,
                runs=runs,
                input=str(row.get("input", "")),
                context=OracleContext(with_prior_state=bool(context_raw.get("with_prior_state", False))),
                expected=OracleExpected(
                    route_kind=str(expected_raw.get("route_kind", "")),
                    should_execute=bool(expected_raw.get("should_execute", False)),
                    has_unhandled_clause=bool(expected_raw.get("has_unhandled_clause", False)),
                    actions=list(expected_raw.get("actions", [])),
                    response_contract=dict(expected_raw.get("response_contract", {})),
                    history=list(expected_raw.get("history", [])),
                ),
            )
        )
    return cases


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "oracle_case" not in metafunc.fixturenames:
        return
    include_full = bool(metafunc.config.getoption("--run-full-oracle"))
    cases = _load_oracle_cases(include_full=include_full)
    metafunc.parametrize("oracle_case", cases, ids=[case["id"] for case in cases])


@pytest.fixture(autouse=True)
def _require_default_llm_configuration() -> None:
    try:
        LLMSettings.from_env()
    except ValidationError as exc:
        provider = get_configured_llm_provider()
        env_var = get_llm_provider_api_key_env(provider)
        msg = exc.errors()[0].get("msg", str(exc)) if exc.errors() else str(exc)
        hint = f" configured provider={provider!r}"
        if env_var is not None:
            hint += f", required key={env_var}"
        pytest.fail(f"Live action oracle tests require default LLM configuration:{hint}. {msg}")


def _fresh_session(*, with_prior_state: bool) -> ReplSession:
    session = ReplSession()
    if with_prior_state:
        session.last_state = {"root_cause": "disk full on orders-api"}
    return session


def _planned_actions_for_case(case: OracleCase) -> tuple[list[dict[str, Any]], bool]:
    decision = route_input(case["input"], _fresh_session(with_prior_state=case["context"]["with_prior_state"]))
    if decision.route_kind == RouteKind.SLASH:
        command = decision.command_text or case["input"].strip()
        parts = command.split()
        return (
            [
                {
                    "kind": "slash",
                    "source": "deterministic",
                    "target_surface": "slash",
                    "command": parts[0] if parts else "",
                    "args": parts[1:] if len(parts) > 1 else [],
                }
            ],
            False,
        )

    llm_plan = plan_actions_with_llm(case["input"])
    assert llm_plan is not None, "Live LLM action planner did not return a parseable plan."
    actions, has_unhandled = llm_plan
    return [normalize_planned_action(action) for action in actions], has_unhandled


def _match_actions(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> bool:
    if len(actual) != len(expected):
        return False
    return all(oracle_action_matches(item, expected[idx]) for idx, item in enumerate(actual))


def _contains_any(haystack: str, needles: list[str]) -> bool:
    if not needles:
        return True
    normalized_needles = [normalize_response_text(needle) for needle in needles if needle.strip()]
    return any(needle in haystack for needle in normalized_needles)


def _history_matches(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> bool:
    if len(actual) != len(expected):
        return False
    for index, expected_item in enumerate(expected):
        if not oracle_action_matches(actual[index], expected_item):
            return False
    return True


def _patch_execution_boundary(
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


def _run_oracle_once(case: OracleCase, monkeypatch: pytest.MonkeyPatch) -> OracleRunResult:
    session = _fresh_session(with_prior_state=case["context"]["with_prior_state"])
    executed: list[dict[str, Any]] = []
    _patch_execution_boundary(monkeypatch, executed)

    console_buffer = io.StringIO()
    console = Console(file=console_buffer, force_terminal=False, highlight=False, width=100)

    decision = route_input(case["input"], session)
    history_start = len(session.history)
    planned_actions, has_unhandled = _planned_actions_for_case(case)
    raw_planner_output = _call_llm(_sanitise_text(case["input"].strip()))

    execute_routed_turn(
        case["input"],
        session,
        console,
        on_exit=lambda: None,
        confirm_fn=lambda _prompt: "y",
        decision=decision,
    )

    expected = case["expected"]
    normalized_response = normalize_response_text(console_buffer.getvalue())
    history_delta = [normalize_history_entry(entry) for entry in session.history[history_start:]]

    executed_match = _match_actions(executed, expected["actions"])
    planned_match = _match_actions(planned_actions, expected["actions"])
    history_match = _history_matches(history_delta, expected["history"])
    any_match = _contains_any(normalized_response, expected["response_contract"].get("any_of_contains", []))
    forbidden = [
        token
        for token in expected["response_contract"].get("must_not_contain", [])
        if normalize_response_text(token) in normalized_response
    ]

    passed = True
    if decision.route_kind.value != expected["route_kind"]:
        passed = False
    if has_unhandled is not expected["has_unhandled_clause"]:
        passed = False
    if not planned_match:
        passed = False
    if expected["should_execute"]:
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
            "id": case["id"],
            "route_kind_actual": decision.route_kind.value,
            "route_kind_expected": expected["route_kind"],
            "has_unhandled_actual": has_unhandled,
            "has_unhandled_expected": expected["has_unhandled_clause"],
            "planned_actions_actual": planned_actions,
            "planned_actions_expected": expected["actions"],
            "executed_actions_actual": executed,
            "executed_actions_expected": expected["actions"],
            "history_actual": history_delta,
            "history_expected": expected["history"],
            "response_normalized": normalized_response,
            "response_contract": expected["response_contract"],
            "forbidden_matches": forbidden,
            "raw_planner_output": raw_planner_output,
        },
    )


def test_router_live_action_oracles(
    oracle_case: OracleCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    runs = max(1, oracle_case["runs"])
    run_results: list[OracleRunResult] = []
    passed_count = 0

    for _ in range(runs):
        run_result = _run_oracle_once(oracle_case, monkeypatch)
        run_results.append(run_result)
        if run_result.passed:
            passed_count += 1

    required = (runs // 2) + 1
    if passed_count >= required:
        return

    artifact_dir = tmp_path_factory.mktemp("router_live_action_oracles")
    artifact_file = Path(artifact_dir) / f"{oracle_case['id']}.json"
    artifact_file.write_text(
        json.dumps([item.details for item in run_results], indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    pytest.fail(
        f"oracle case {oracle_case['id']!r} failed {runs - passed_count}/{runs} runs; "
        f"artifact: {artifact_file}"
    )
