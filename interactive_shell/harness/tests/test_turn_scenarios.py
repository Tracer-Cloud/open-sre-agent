"""Canonical turn scenario tests (deterministic + live LLM)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

import pytest
from rich.console import Console

from core.runtime import Agent, AgentTool, AgentToolContext
from core.runtime.llm.agent_llm_client import ToolCall
from interactive_shell.command_registry import SLASH_COMMANDS
from interactive_shell.harness.orchestration.action_prompt import (
    build_action_system_prompt,
    build_action_user_message,
)
from interactive_shell.harness.orchestration.command_dispatch import (
    deterministic_command_text,
)
from interactive_shell.harness.orchestration.feature_flags import (
    investigation_loop_enabled,
)
from interactive_shell.harness.orchestration.interaction_models import (
    ActionKind,
    default_target_surface,
)
from interactive_shell.harness.orchestration.tool_contracts import ToolContext
from interactive_shell.harness.orchestration.tool_registry import (
    ACTION_KIND_TO_TOOL,
    REGISTRY,
)
from interactive_shell.harness.tests._ci_gates import (
    skip_investigation_loop_disabled,
    skip_or_fail,
)
from interactive_shell.harness.tests._oracle_normalize import cli_command_payload_matches
from interactive_shell.harness.tests._oracle_runtime import (
    LIVE_INTEGRATION_SENTINEL,
    OracleRunResult,
    fresh_session,
    resolve_live_integrations,
    run_oracle_once,
    session_capabilities,
)
from interactive_shell.harness.tests.scenario_loader import (
    ScenarioCase,
    iter_scenarios_for_shard,
    load_all_scenarios,
    read_shard_config,
)


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
_TOOL_TO_ACTION_KIND = {tool: kind for kind, tool in ACTION_KIND_TO_TOOL.items()}
_LIVE_PLANNING_MAX_ITERATIONS = 3


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
        skip_investigation_loop_disabled()


def _skip_if_live_integrations_unavailable(case: ScenarioCase) -> None:
    """Skip scenarios that need a real credentialed integration we can't resolve.

    Scenarios that pin ``<service>: "@live"`` in ``resolved_integrations`` make
    real calls during the gather loop. When **every** @live service is
    unavailable the scenario is skipped locally (env gap). In CI the same
    condition fails the job so @live gather scenarios cannot pass silently.
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
        skip_or_fail(
            "Live integration credentials unavailable for all @live services: "
            + ", ".join(sorted(live_services))
            + ". Configure at least one integration in the local store/env or provide CI "
            "secrets (e.g. DD_API_KEY/DD_APP_KEY, GRAFANA_READ_TOKEN, SENTRY_AUTH_TOKEN) "
            "to run this scenario."
        )


def _build_actual_action(action: ToolCall) -> ExpectedAction:
    kind = _TOOL_TO_ACTION_KIND.get(action.name)
    if kind is None:
        msg = f"Unexpected action tool call: {action.name!r}"
        raise AssertionError(msg)
    typed_kind = cast(ActionKind, kind)
    content = _content_from_tool_call(typed_kind, action.input)
    expected: ExpectedAction = {
        "kind": typed_kind,
        "content": content,
        "source": "llm",
        "target_surface": default_target_surface(typed_kind) or "",
    }
    if typed_kind == "slash":
        command = str(action.input.get("command", "")).strip()
        raw_args = action.input.get("args", [])
        args = [str(arg).strip() for arg in raw_args] if isinstance(raw_args, list) else []
        expected["command"] = command
        expected["args"] = args
    elif typed_kind == "cli_command":
        expected["payload"] = content
    elif typed_kind == "synthetic_test":
        suite, _sep, scenario = content.partition(":")
        expected["suite"] = suite
        expected["scenario"] = scenario
    elif typed_kind == "sample_alert":
        # ``template`` is the tool's required arg; fixtures include it
        # alongside ``content`` for explicitness — mirror that shape.
        template_value = action.input.get("template")
        expected["template"] = (
            str(template_value).strip() if isinstance(template_value, str) else content
        )
    return expected


def _planning_probe_tool(tool: AgentTool) -> AgentTool:
    """Return an inert copy of an action tool for live planning assertions."""

    def _execute(args: dict[str, Any], _ctx: AgentToolContext) -> dict[str, Any]:
        if tool.name == "slash_invoke":
            command = str(args.get("command", "")).strip()
            raw_args = args.get("args")
            parsed_args = (
                [str(item).strip() for item in raw_args] if isinstance(raw_args, list) else []
            )
            content = _slash_content(command, parsed_args)
        elif tool.name == "investigation_start":
            content = str(args.get("alert_text", "")).strip()
        elif tool.name == "synthetic_run":
            suite = str(args.get("suite", "")).strip()
            scenario = str(args.get("scenario", "")).strip()
            content = f"{suite}:{scenario}" if scenario else suite
        else:
            content = tool.name
        return {"ok": True, "text": f"planned {content}".strip()}

    return AgentTool(
        name=tool.name,
        description=tool.description,
        input_schema=tool.public_input_schema,
        execute=_execute,
        source=tool.source,
        parallel_safe=tool.parallel_safe,
    )


def _content_from_tool_call(kind: ActionKind, args: dict[str, object]) -> str:
    if kind == "slash":
        command = str(args.get("command", "")).strip()
        raw_args = args.get("args", [])
        parsed_args = [str(arg).strip() for arg in raw_args] if isinstance(raw_args, list) else []
        return _slash_content(command, parsed_args)
    if kind == "llm_provider":
        return str(args.get("target", args.get("provider", ""))).strip()
    if kind == "shell":
        return str(args.get("command", "")).strip()
    if kind == "sample_alert":
        return str(args.get("template", "")).strip()
    if kind == "investigation":
        return str(args.get("alert_text", "")).strip()
    if kind == "synthetic_test":
        suite = str(args.get("suite", "")).strip()
        scenario = str(args.get("scenario", "")).strip()
        return f"{suite}:{scenario}" if scenario else suite
    if kind == "task_cancel":
        return str(args.get("target", "")).strip()
    if kind == "cli_command":
        return str(args.get("payload", "")).strip()
    if kind == "implementation":
        return str(args.get("task", "")).strip()
    if kind == "assistant_handoff":
        return str(args.get("content", "")).strip()
    return ""


def _action_match_view(action: ExpectedAction) -> ExpectedAction:
    """Ignore action provenance; live tests assert behavior, not selector path."""
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


def _expected_actions_are_assistant_handoff_only(
    expected_actions: list[ExpectedAction],
) -> bool:
    return bool(expected_actions) and all(
        str(action.get("kind", "")).strip() == "assistant_handoff" for action in expected_actions
    )


def _planning_actions_for_match(
    actual_actions: list[ExpectedAction],
    expected_actions: list[ExpectedAction],
) -> list[ExpectedAction]:
    if _expected_actions_are_assistant_handoff_only(expected_actions) and all(
        str(action.get("kind", "")).strip() == "assistant_handoff" for action in actual_actions
    ):
        return actual_actions[: len(expected_actions)]
    if any(
        str(action.get("kind", "")).strip() == "assistant_handoff" for action in expected_actions
    ):
        return actual_actions
    return [
        action
        for action in actual_actions
        if str(action.get("kind", "")).strip() != "assistant_handoff"
    ]


def _no_tool_response_is_handoff_equivalent(
    actual_actions: list[ExpectedAction],
    expected_actions: list[ExpectedAction],
) -> bool:
    # A planner that emits no action tool calls falls through to the conversational
    # assistant. For live LLM planning tests, that is behaviorally equivalent to
    # an assistant_handoff-only plan and avoids flaking on harmless provider
    # differences. Executable expectations still require exact tool calls.
    return not actual_actions and _expected_actions_are_assistant_handoff_only(expected_actions)


def test_no_tool_response_equivalence_is_limited_to_assistant_handoff() -> None:
    handoff_expected = cast(
        "list[ExpectedAction]",
        [{"kind": "assistant_handoff", "content": "answer from chat", "source": "llm"}],
    )
    slash_expected = cast(
        "list[ExpectedAction]",
        [{"kind": "slash", "content": "/health", "command": "/health", "args": []}],
    )

    assert _no_tool_response_is_handoff_equivalent([], handoff_expected)
    assert not _no_tool_response_is_handoff_equivalent([], slash_expected)


def test_planning_match_ignores_handoff_after_terminal_action_only() -> None:
    actual = cast(
        "list[ExpectedAction]",
        [
            {"kind": "slash", "content": "/health", "command": "/health", "args": []},
            {"kind": "assistant_handoff", "content": "done", "source": "llm"},
        ],
    )
    slash_expected = cast(
        "list[ExpectedAction]",
        [{"kind": "slash", "content": "/health", "command": "/health", "args": []}],
    )
    handoff_expected = cast(
        "list[ExpectedAction]",
        [{"kind": "assistant_handoff", "content": "answer from chat", "source": "llm"}],
    )

    assert _planning_actions_for_match(actual, slash_expected) == actual[:1]
    assert _planning_actions_for_match(actual, handoff_expected) == actual


def test_planning_match_collapses_handoff_only_retries() -> None:
    actual = cast(
        "list[ExpectedAction]",
        [
            {"kind": "assistant_handoff", "content": "first", "source": "llm"},
            {"kind": "assistant_handoff", "content": "second", "source": "llm"},
        ],
    )
    handoff_expected = cast(
        "list[ExpectedAction]",
        [{"kind": "assistant_handoff", "content": "answer from chat", "source": "llm"}],
    )

    assert _planning_actions_for_match(actual, handoff_expected) == actual[:1]


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
    skip_or_fail(f"No turn cases selected for shard {index}/{total}.")


def test_deterministic_command_text_matches_scenario(deterministic_case: ScenarioCase) -> None:
    prompt = deterministic_case.scenario.input.prompt
    answer = deterministic_case.answer

    # The literal-command detector must reproduce the normalized slash command
    # the scenario expects for UI policy decisions.
    assert deterministic_command_text(prompt) == answer.turn.expected_command_text


def test_help_normalizes_to_slash_help_deterministically() -> None:
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

    ctx = ToolContext(session=session, console=Console(file=io.StringIO(), force_terminal=False))
    tools = REGISTRY.agent_tools_for_context(ctx)
    from core.runtime.llm import agent_llm_client

    llm = agent_llm_client.get_agent_llm()
    result = Agent(
        llm=llm,
        system=build_action_system_prompt(session),
        tools=[_planning_probe_tool(tool) for tool in tools],
        resolved_integrations={},
        max_iterations=_LIVE_PLANNING_MAX_ITERATIONS,
    ).run([{"role": "user", "content": build_action_user_message(prompt)}])
    actions = [tool_call for tool_call, _output in result.executed]
    actual_actions = [_build_actual_action(action) for action in actions]
    expected_actions = cast("list[ExpectedAction]", [dict(item) for item in answer.planned_actions])
    actual_actions_for_match = _planning_actions_for_match(actual_actions, expected_actions)

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

    handoff_only = bool(actions) and all(action.name == "assistant_handoff" for action in actions)
    # When the fixture specifies planned_actions: [] it means "no executable
    # action expected". A planner response that consists solely of
    # assistant_handoff actions is semantically equivalent and is accepted
    # without a mismatch assertion. Any other actual actions (slash, shell …)
    # with an empty fixture still fall through and fail the match.
    if (not expected_actions and handoff_only) or _no_tool_response_is_handoff_equivalent(
        actual_actions_for_match,
        expected_actions,
    ):
        pass
    else:
        _assert_planned_actions_match(actual_actions_for_match, expected_actions)


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

    artifact_dir = tmp_path_factory.mktemp("turn_live_action_planning")
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
    artifact_dir = tmp_path_factory.mktemp("turn_live_action_oracles")
    artifact_file = Path(artifact_dir) / f"{live_oracle_case.scenario.id}.json"
    artifact_file.write_text(
        json.dumps([item.details for item in run_results], indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    pytest.fail(
        f"oracle case {live_oracle_case.scenario.id!r} failed {runs - passed_count}/{runs} runs; "
        f"artifact: {artifact_file}; failed_details={json.dumps(failed_details, ensure_ascii=True)}"
    )
