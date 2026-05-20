"""Canonical routing scenario tests (deterministic + live LLM)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import pytest

from app.cli.interactive_shell.commands import SLASH_COMMANDS
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm,
)
from app.cli.interactive_shell.routing.router import classify_input, route_input
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
    template: NotRequired[str]


_ALL_CASES = load_all_scenarios()
_DETERMINISTIC_CASES = [
    case for case in _ALL_CASES if case.scenario.intent_class == "deterministic"
]
_LIVE_CASES = iter_scenarios_for_shard(
    [case for case in _ALL_CASES if case.scenario.intent_class != "deterministic"]
)


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
    elif action.kind == "sample_alert":
        # ``template`` is the tool's required arg; fixtures include it
        # alongside ``content`` for explicitness — mirror that shape.
        template_value = action.args.get("template") if action.args else None
        expected["template"] = (
            str(template_value).strip() if isinstance(template_value, str) else action.content
        )
    return expected


def _action_match_view(action: ExpectedAction) -> ExpectedAction:
    """Ignore action provenance; live tests assert behavior, not planner path."""
    return cast(
        ExpectedAction,
        {key: value for key, value in action.items() if key != "source"},
    )


def _assert_planned_actions_match(
    actual_actions: list[ExpectedAction],
    expected_actions: list[ExpectedAction],
) -> None:
    assert len(actual_actions) == len(expected_actions)
    for index, expected in enumerate(expected_actions):
        actual = actual_actions[index]
        if str(expected.get("kind", "")) != "assistant_handoff":
            assert _action_match_view(actual) == _action_match_view(expected)
            continue
        assert actual.get("kind") == "assistant_handoff"
        expected_source = str(expected.get("source", "")).strip()
        if expected_source:
            assert actual.get("source") == expected_source
        content = str(actual.get("content", "")).strip()
        assert content, f"assistant_handoff action {index} must include text content."


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "deterministic_case" in metafunc.fixturenames:
        metafunc.parametrize(
            "deterministic_case",
            _DETERMINISTIC_CASES,
            ids=[case.scenario.id for case in _DETERMINISTIC_CASES],
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


def test_shard_selection_is_non_empty() -> None:
    if _LIVE_CASES:
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
def test_live_action_planning(live_planning_case: ScenarioCase) -> None:
    session = fresh_session(
        with_prior_state=live_planning_case.scenario.session.has_prior_state,
        configured_integrations=live_planning_case.scenario.session.configured_integrations,
        available_capabilities={
            "slash_commands": live_planning_case.scenario.available_capabilities.slash_commands,
            "cli_commands": live_planning_case.scenario.available_capabilities.cli_commands,
            "synthetic_suites": live_planning_case.scenario.available_capabilities.synthetic_suites,
        },
    )
    prompt = live_planning_case.scenario.input.prompt
    answer = live_planning_case.answer

    decision = route_input(prompt, session)
    assert decision.route_kind.value == answer.route.expected_kind

    llm_plan = plan_actions_with_llm(prompt, session=session)
    assert llm_plan is not None, "Live LLM action planner did not return a parseable plan."
    actions, _has_unhandled = llm_plan
    actual_actions = [_build_actual_action(action) for action in actions]
    expected_actions = cast("list[ExpectedAction]", [dict(item) for item in answer.planned_actions])

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

    handoff_only = bool(actions) and all(action.kind == "assistant_handoff" for action in actions)
    # When the fixture specifies planned_actions: [] it means "no executable
    # action expected". A planner response that consists solely of
    # assistant_handoff actions is semantically equivalent and is accepted
    # without a mismatch assertion. Any other actual actions (slash, shell …)
    # with an empty fixture still fall through and fail the match.
    if not expected_actions and handoff_only:
        pass
    else:
        _assert_planned_actions_match(actual_actions, expected_actions)

    # Response-contract assertions (``must_contain_any`` / ``must_not_contain``)
    # are checked against the rendered terminal response in
    # ``test_live_turn_execution_oracle``. They are intentionally not
    # asserted here: the planner emits an *intent hint* in
    # ``assistant_handoff.content`` which the assistant uses to ground its
    # reply, not the reply itself, so applying the response contract to
    # the planning hint over-constrains LLM phrasing without testing the
    # user-visible behavior.


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

    failed_details = [item.details for item in run_results if not item.passed]
    artifact_dir = tmp_path_factory.mktemp("router_live_action_oracles")
    artifact_file = Path(artifact_dir) / f"{live_oracle_case.scenario.id}.json"
    artifact_file.write_text(
        json.dumps([item.details for item in run_results], indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    pytest.fail(
        f"oracle case {live_oracle_case.scenario.id!r} failed {runs - passed_count}/{runs} runs; "
        f"artifact: {artifact_file}; failed_details={json.dumps(failed_details, ensure_ascii=True)}"
    )
