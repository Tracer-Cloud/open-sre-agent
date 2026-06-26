"""Canonical routing scenario tests (deterministic + live LLM)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import pytest

from cli.interactive_shell.command_registry import SLASH_COMMANDS
from cli.interactive_shell.routing.handle_message_with_agent.command_dispatch import (
    deterministic_command_text,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.feature_flags import (
    investigation_loop_enabled,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm,
)
from cli.interactive_shell.routing.router import RouteKind, route_input
from cli.interactive_shell.routing.tests._oracle_normalize import cli_command_payload_matches
from cli.interactive_shell.routing.tests._oracle_runtime import (
    LIVE_INTEGRATION_SENTINEL,
    OracleRunResult,
    fresh_session,
    resolve_live_integrations,
    run_oracle_once,
    session_capabilities,
)
from cli.interactive_shell.routing.tests.scenario_loader import (
    ScenarioCase,
    iter_scenarios_for_shard,
    load_all_scenarios,
    read_shard_config,
)
from cli.interactive_shell.runtime.session import ReplSession


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


def _expects_investigation(case: ScenarioCase) -> bool:
    """True when a scenario expects the planner to dispatch a natural-language
    investigation (``investigation_start``).

    The investigation loop can be disabled in the interactive shell via
    ``feature_flags.INTERACTIVE_SHELL_INVESTIGATION_ENABLED``. When it is off the
    planner is not offered ``investigation_start``, so these scenarios no longer
    apply and are skipped rather than asserted against the old behavior. Sample
    alerts and synthetic runs are unaffected.
    """
    actions = (*case.answer.planned_actions, *case.answer.executed_actions)
    return any(str(action.get("kind", "")).strip() == "investigation" for action in actions)


def _skip_if_investigation_disabled(case: ScenarioCase) -> None:
    if not investigation_loop_enabled() and _expects_investigation(case):
        pytest.skip(
            "Natural-language investigation loop is disabled in the interactive shell "
            "(feature_flags.INTERACTIVE_SHELL_INVESTIGATION_ENABLED is False); "
            "this investigation scenario does not apply. Re-enable the flag to run it."
        )


def _skip_if_live_integrations_unavailable(case: ScenarioCase) -> None:
    """Skip scenarios that need a real credentialed integration we can't resolve.

    Scenarios that pin ``<service>: "@live"`` in ``resolved_integrations`` make
    real calls during the gather loop. When **every** @live service is
    unavailable the scenario is skipped — an environment gap, not a routing
    regression. Fixtures 333–335 pin Datadog @live and assert
    ``must_return_valid_data_any`` on a Datadog gather tool (316-style).
    """
    override = case.scenario.session.resolved_integrations
    if not override:
        return
    live_services = [
        service for service, config in override.items() if config == LIVE_INTEGRATION_SENTINEL
    ]
    if not live_services:
        return
    _expanded, unavailable = resolve_live_integrations(override)
    if len(unavailable) >= len(live_services):
        pytest.skip(
            "Live integration credentials unavailable for all @live services: "
            + ", ".join(sorted(live_services))
            + ". Configure at least one integration in the local store/env or provide CI "
            "secrets (e.g. DD_API_KEY/DD_APP_KEY, GRAFANA_READ_TOKEN, SENTRY_AUTH_TOKEN) "
            "to run this scenario."
        )


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
        expected_kind = str(expected.get("kind", ""))
        if expected_kind == "assistant_handoff":
            assert actual.get("kind") == "assistant_handoff"
            expected_source = str(expected.get("source", "")).strip()
            if expected_source:
                assert actual.get("source") == expected_source
            content = str(actual.get("content", "")).strip()
            assert content, f"assistant_handoff action {index} must include text content."
            continue
        # A synthesized investigation (no pasted/quoted payload) carries freeform
        # alert_text that varies per live run. When the fixture leaves content
        # empty, assert kind + non-empty alert_text rather than exact equality;
        # fixtures that pin a verbatim payload (e.g. a pasted alert) keep the
        # strict match below.
        if expected_kind == "investigation" and not str(expected.get("content", "")).strip():
            assert actual.get("kind") == "investigation"
            content = str(actual.get("content", "")).strip()
            assert content, f"investigation action {index} must include synthesized alert_text."
            continue
        if expected_kind == "cli_command":
            assert actual.get("kind") == "cli_command"
            actual_payload = str(actual.get("payload", "")).strip()
            expected_payload = str(expected.get("payload", "")).strip()
            assert actual_payload, f"cli_command action {index} must include payload."
            assert cli_command_payload_matches(actual_payload, expected_payload), (
                f"cli_command action {index} payload mismatch: "
                f"{actual_payload!r} vs {expected_payload!r}"
            )
            continue
        assert _action_match_view(actual) == _action_match_view(expected)


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

    # Routing is single-branch: every turn is handed to the agent.
    decision = route_input(prompt, session)
    assert decision.route_kind is RouteKind.HANDLE_MESSAGE_WITH_AGENT

    # Deterministic command dispatch is the agent's pre-LLM fast path; it must
    # reproduce the normalized slash command the scenario expects.
    assert deterministic_command_text(prompt) == answer.route.expected_command_text


def test_help_route_decision_has_structured_shape() -> None:
    session = ReplSession()
    decision = route_input("/help", session)

    assert decision.to_event_payload() == {
        "route_kind": "handle_message_with_agent",
        "confidence": 1.0,
        "matched_signals": "",
        "fallback_reason": "",
    }
    # The agent fast path dispatches the literal slash command deterministically.
    assert deterministic_command_text("/help") == "/help"


def _assert_live_action_planning_once(case: ScenarioCase) -> None:
    resolved_override, _unavailable = resolve_live_integrations(
        case.scenario.session.resolved_integrations
    )
    session = fresh_session(
        with_prior_state=case.scenario.session.has_prior_state,
        configured_integrations=case.scenario.session.configured_integrations,
        available_capabilities=session_capabilities(case.scenario.available_capabilities),
        resolved_integrations_override=resolved_override,
    )
    prompt = case.scenario.input.prompt
    answer = case.answer

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


@pytest.mark.integration
@pytest.mark.live_llm
def test_live_action_planning(
    live_planning_case: ScenarioCase,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Assert live LLM action plans match fixture expectations.

    Response-contract assertions are checked in ``test_live_turn_execution_oracle``;
    here we only validate the planner's action list, with majority voting when a
    fixture sets ``runs > 1`` (same flake tolerance as the execution oracle).
    """
    _skip_if_investigation_disabled(live_planning_case)
    runs = max(1, live_planning_case.answer.runs)
    failures: list[str] = []
    passed_count = 0

    for _ in range(runs):
        try:
            _assert_live_action_planning_once(live_planning_case)
        except AssertionError as exc:
            failures.append(str(exc))
        else:
            passed_count += 1

    required = (runs // 2) + 1
    if passed_count >= required:
        return

    artifact_dir = tmp_path_factory.mktemp("router_live_action_planning")
    artifact_file = Path(artifact_dir) / f"{live_planning_case.scenario.id}.json"
    artifact_file.write_text(
        json.dumps(failures, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    pytest.fail(
        f"planning case {live_planning_case.scenario.id!r} failed "
        f"{runs - passed_count}/{runs} runs; artifact: {artifact_file}; "
        f"failures={json.dumps(failures, ensure_ascii=True)}"
    )


@pytest.mark.integration
@pytest.mark.live_llm
def test_live_turn_execution_oracle(
    live_oracle_case: ScenarioCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    _skip_if_investigation_disabled(live_oracle_case)
    _skip_if_live_integrations_unavailable(live_oracle_case)
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
