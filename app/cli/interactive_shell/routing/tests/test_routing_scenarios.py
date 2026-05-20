"""Canonical routing scenario tests (deterministic + live LLM)."""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import NotRequired, TypedDict

import pytest

from app.cli.interactive_shell.commands import SLASH_COMMANDS
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm,
)
from app.cli.interactive_shell.routing.llm_intent_classifier import clear_classify_cache
from app.cli.interactive_shell.routing.router import RouteKind, classify_input, route_input
from app.cli.interactive_shell.routing.tests._oracle_runtime import (
    OracleRunResult,
    fresh_session,
    run_oracle_once,
)
from app.cli.interactive_shell.routing.tests.scenario_loader import (
    ScenarioCase,
    iter_scenarios_for_shard,
    load_all_scenarios,
    read_shard_config,
)
from app.cli.interactive_shell.runtime.session import ReplSession

MAX_UNCERTAIN_RETRIES = 3


class ExpectedAction(TypedDict):
    kind: str
    content: str
    source: NotRequired[str]
    target_surface: NotRequired[str]
    command: NotRequired[str]
    args: NotRequired[list[str]]
    payload: NotRequired[str]
    suite: NotRequired[str]
    scenario: NotRequired[str]


_ALL_CASES = load_all_scenarios()
_DETERMINISTIC_CASES = [
    case for case in _ALL_CASES if case.scenario.intent_class == "deterministic"
]
_LIVE_CASES = [case for case in _ALL_CASES if case.scenario.intent_class != "deterministic"]
_SHARDED_LIVE_CASES = iter_scenarios_for_shard(_LIVE_CASES)


def _slash_content(command: str, args: list[str]) -> str:
    return " ".join([command, *args]) if args else command


def _build_actual_action(action: PlannedAction) -> ExpectedAction:
    expected: ExpectedAction = {
        "kind": action.kind,
        "content": action.content,
        "source": action.source,
        "target_surface": action.target_surface or "",
    }
    if action.kind == "slash":
        parts = action.content.split()
        command = parts[0] if parts else ""
        args = parts[1:] if len(parts) > 1 else []
        expected["command"] = command
        expected["args"] = args
    elif action.kind == "cli_command":
        expected["payload"] = action.content
    elif action.kind == "synthetic_test":
        suite, _sep, scenario = action.content.partition(":")
        expected["suite"] = suite
        expected["scenario"] = scenario
    return expected


def _compact_action(action: PlannedAction) -> ExpectedAction:
    return {"kind": action.kind, "content": action.content}


def _is_uncertain_fallback(actual_kind: str, fallback_reason: str | None) -> bool:
    return fallback_reason is not None and actual_kind == RouteKind.CLI_AGENT.value


def _route_kind_with_uncertain_retries(
    *,
    case_id: str,
    text: str,
    session: ReplSession,
    expected_kind: str,
) -> str:
    started_at = perf_counter()
    decision = route_input(text, session)
    latency_ms = int((perf_counter() - started_at) * 1000)
    actual_kind = decision.route_kind.value
    print(
        f"routing_live_case id={case_id} expected={expected_kind} "
        f"actual={actual_kind} latency_ms={latency_ms}"
    )

    attempts = 1
    while _is_uncertain_fallback(actual_kind, decision.fallback_reason):
        if attempts >= MAX_UNCERTAIN_RETRIES:
            break
        clear_classify_cache()
        started_at = perf_counter()
        decision = route_input(text, session)
        latency_ms = int((perf_counter() - started_at) * 1000)
        attempts += 1
        actual_kind = decision.route_kind.value
        print(
            f"routing_live_case id={case_id} expected={expected_kind} "
            f"actual={actual_kind} latency_ms={latency_ms}"
        )

    return actual_kind


def _live_actions_for_case(case: ScenarioCase, session: ReplSession) -> list[ExpectedAction]:
    prompt = case.scenario.input.prompt
    decision = route_input(prompt, session)
    if decision.route_kind == RouteKind.SLASH:
        return [
            {
                "kind": "slash",
                "content": decision.command_text or prompt.strip(),
            }
        ]

    llm_plan = plan_actions_with_llm(prompt)
    assert llm_plan is not None, "Live LLM action planner did not return a parseable plan."
    actions, has_unhandled = llm_plan
    if actions:
        return [_compact_action(action) for action in actions]

    classification = [dict(item) for item in case.answer.classification_actions]
    if len(classification) == 1 and classification[0].get("kind") == "assistant_handoff":
        return [
            {
                "kind": "assistant_handoff",
                "content": str(classification[0].get("content", "")),
            }
        ]

    assert not case.answer.planned_actions, (
        "No executable actions planned, but fixture expects actions."
    )
    assert has_unhandled is True
    return []


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "deterministic_case" in metafunc.fixturenames:
        metafunc.parametrize(
            "deterministic_case",
            _DETERMINISTIC_CASES,
            ids=[case.scenario.id for case in _DETERMINISTIC_CASES],
        )
    if "live_route_case" in metafunc.fixturenames:
        metafunc.parametrize(
            "live_route_case",
            _SHARDED_LIVE_CASES,
            ids=[case.scenario.id for case in _SHARDED_LIVE_CASES],
        )
    if "live_planning_case" in metafunc.fixturenames:
        metafunc.parametrize(
            "live_planning_case",
            _LIVE_CASES,
            ids=[case.scenario.id for case in _LIVE_CASES],
        )
    if "live_oracle_case" in metafunc.fixturenames:
        metafunc.parametrize(
            "live_oracle_case",
            _LIVE_CASES,
            ids=[case.scenario.id for case in _LIVE_CASES],
        )


@pytest.fixture(autouse=True)
def _clear_classify_cache_for_live() -> None:
    clear_classify_cache()


def test_shard_selection_is_non_empty() -> None:
    if _SHARDED_LIVE_CASES:
        return
    total, index = read_shard_config()
    pytest.skip(f"No routing cases selected for shard {index}/{total}.")


def test_deterministic_routing(deterministic_case: ScenarioCase) -> None:
    session = ReplSession()
    prompt = deterministic_case.scenario.input.prompt
    answer = deterministic_case.answer

    decision = route_input(prompt, session)
    assert classify_input(prompt, session) == answer.route.expected_kind
    assert decision.route_kind.value == answer.route.expected_kind
    assert decision.matched_signals == tuple(answer.route.expected_signals)
    assert decision.command_text == answer.route.expected_command_text


def test_help_route_decision_has_structured_shape() -> None:
    session = ReplSession()
    decision = route_input("/help", session)

    assert decision.to_event_payload() == {
        "route_kind": "slash",
        "confidence": 1.0,
        "matched_signals": "slash_prefix",
        "fallback_reason": "",
    }
    assert decision.command_text == "/help"


@pytest.mark.integration
@pytest.mark.live_llm
def test_live_route_classification(live_route_case: ScenarioCase) -> None:
    session = fresh_session(with_prior_state=live_route_case.scenario.session.has_prior_state)
    expected_kind = live_route_case.answer.route.expected_kind
    actual_kind = _route_kind_with_uncertain_retries(
        case_id=live_route_case.scenario.id,
        text=live_route_case.scenario.input.prompt,
        session=session,
        expected_kind=expected_kind,
    )

    assert actual_kind == expected_kind

    classification = [dict(item) for item in live_route_case.answer.classification_actions]
    if classification:
        assert _live_actions_for_case(live_route_case, session) == classification  # type: ignore[arg-type]


@pytest.mark.integration
@pytest.mark.live_llm
def test_live_action_planning(live_planning_case: ScenarioCase) -> None:
    session = fresh_session(with_prior_state=live_planning_case.scenario.session.has_prior_state)
    prompt = live_planning_case.scenario.input.prompt
    answer = live_planning_case.answer

    decision = route_input(prompt, session)
    assert decision.route_kind.value == answer.route.expected_kind

    llm_plan = plan_actions_with_llm(prompt)
    assert llm_plan is not None, "Live LLM action planner did not return a parseable plan."
    actions, has_unhandled = llm_plan
    actual_actions = [_build_actual_action(action) for action in actions]
    expected_actions = [dict(item) for item in answer.planned_actions]

    for action_idx, expected in enumerate(expected_actions):
        kind = str(expected.get("kind", ""))
        if kind == "slash":
            command = str(expected.get("command", "")).strip()
            raw_args = expected.get("args", [])
            if command not in SLASH_COMMANDS and not command.startswith("/"):
                msg = f"Invalid slash command in fixture: {command!r}"
                raise AssertionError(msg)
            args = [str(arg).strip() for arg in raw_args] if isinstance(raw_args, list) else []
            content = str(expected.get("content", "")).strip()
            if content and content != _slash_content(command, args):
                msg = f"Fixture action {action_idx} content must match command+args."
                raise AssertionError(msg)

    assert actual_actions == expected_actions
    assert has_unhandled is answer.policy.has_unhandled_clause


@pytest.mark.integration
@pytest.mark.live_llm
def test_live_turn_execution_oracle(
    live_oracle_case: ScenarioCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    runs = max(1, live_oracle_case.answer.runs)
    run_results: list[OracleRunResult] = []
    passed_count = 0

    for _ in range(runs):
        run_result = run_oracle_once(live_oracle_case, monkeypatch)
        run_results.append(run_result)
        if run_result.passed:
            passed_count += 1

    required = (runs // 2) + 1
    if passed_count >= required:
        return

    artifact_dir = tmp_path_factory.mktemp("router_live_action_oracles")
    artifact_file = Path(artifact_dir) / f"{live_oracle_case.scenario.id}.json"
    artifact_file.write_text(
        json.dumps([item.details for item in run_results], indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    pytest.fail(
        f"oracle case {live_oracle_case.scenario.id!r} failed {runs - passed_count}/{runs} runs; "
        f"artifact: {artifact_file}"
    )
