"""Canonical turn scenario tests (live LLM planning and oracle)."""

from __future__ import annotations

import io
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

import pytest
from rich.console import Console

from core import Agent, AgentTool, AgentToolContext
from core.agent_harness.prompts import (
    build_action_system_prompt,
    build_action_user_message,
)
from core.agent_harness.tools.action_tools import get_action_tools_from_integrations_view
from core.agent_harness.tools.tool_context import ActionToolScope
from core.agent_harness.turns.action_driver import _MAX_TOOL_CALLING_ITERATIONS
from core.llm.shared.llm_retry import LLMCreditExhaustedError
from core.llm.types import ToolCall
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS
from tests.core.agent._ci_gates import (
    skip_or_fail,
)
from tests.core.agent._oracle_normalize import cli_command_payload_matches
from tests.core.agent._oracle_runtime import (
    LIVE_INTEGRATION_SENTINEL,
    OracleRunResult,
    normalize_executed_actions_for_oracle_match,
    resolve_live_integrations,
    run_oracle_once,
    session_capabilities,
    session_from_scenario,
)
from tests.core.agent._planned_action import default_target_surface
from tests.core.agent.scenario_loader import (
    ScenarioCase,
    effective_runs,
    is_full_selection,
    iter_scenarios_for_shard,
    load_all_scenarios,
    read_shard_config,
    select_cases,
    select_representative,
)
from tools.interactive_shell.action_names import TOOL_KIND_TO_NAME, ActionToolName, ToolKind
from tools.interactive_shell.actions.investigation import normalize_investigation_alert_text


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
    evidence_kind: NotRequired[str]
    session_goal: NotRequired[bool]
    session_goal_items: NotRequired[list[str]]


_ALL_CASES = load_all_scenarios()
# Default gate: a small, representative downsample applied everywhere (local and
# CI) unless an explicit selection (``--turn-select`` / ``TURN_SELECT``) opts in
# to a different subset or the full suite (``TURN_SELECT=all``). The gate is then
# sharded so each CI piece stays tiny.
_DEFAULT_GATE_CASES = select_representative(_ALL_CASES)
_LIVE_CASES = iter_scenarios_for_shard(_DEFAULT_GATE_CASES)
_NAME_TO_TOOL_KIND = {tool: kind for kind, tool in TOOL_KIND_TO_NAME.items()}
# Playbook / session-meta tools the live planner may emit before a scenario
# kind. They are not ToolKind members, so fixtures never list them.
_PLANNING_PRELUDE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        ActionToolName.ASK_USER_CHOICE,
        ActionToolName.SKILL_VIEW,
        ActionToolName.UPDATE_PLAN,
    }
)
# Mirror the production action loop budget so live planning can exercise the same
# multi-step, data-dependent compound chains the real gateway/REPL turns allow,
# instead of drifting from a stale hardcoded cap.
_LIVE_PLANNING_MAX_ITERATIONS = _MAX_TOOL_CALLING_ITERATIONS
_CREDIT_EXHAUSTED_MARKERS = (
    "credit exhausted",
    "credit balance is too low",
    "credit balance too low",
    "credit_balance_exhausted",
    "no credits remaining",
    "insufficient_quota",
    "billing_hard_limit_reached",
)

# SDK init / auth failures that mean "no usable key", not a planner assertion.
_MISSING_CREDENTIAL_MARKERS = (
    "missing credentials",
    "invalid_api_key",
    "incorrect api key",
    "authenticationerror",
    "could not resolve credentials",
)


def _provider_credit_exhausted_message(text: str) -> bool:
    normalized = text.lower()
    return any(marker in normalized for marker in _CREDIT_EXHAUSTED_MARKERS)


def _missing_llm_credentials_message(text: str) -> bool:
    normalized = text.lower()
    return any(marker in normalized for marker in _MISSING_CREDENTIAL_MARKERS)


def _skip_or_fail_provider_credit_exhausted(message: str) -> None:
    skip_or_fail(
        "Live LLM provider credit/quota is exhausted; cannot verify live turn "
        f"scenario behavior. {message}"
    )


def _skip_or_fail_missing_llm_credentials(message: str) -> None:
    skip_or_fail(
        "Live LLM credentials are missing or unusable; cannot verify live turn "
        f"scenario behavior. {message}"
    )


def _slash_content(command: str, args: list[str]) -> str:
    return " ".join([command, *args]) if args else command


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
    kind = _NAME_TO_TOOL_KIND.get(action.name)
    if kind is None:
        msg = f"Unexpected action tool call: {action.name!r}"
        raise AssertionError(msg)
    typed_kind = cast(ToolKind, kind)
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


def _scenario_tool_calls(actions: Sequence[ToolCall]) -> list[ToolCall]:
    """Drop playbook/session-meta calls before matching fixture planned_actions."""
    return [action for action in actions if action.name not in _PLANNING_PRELUDE_TOOL_NAMES]


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
            content = normalize_investigation_alert_text(str(args.get("alert_text", "")))
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


def _content_from_tool_call(kind: ToolKind, args: dict[str, object]) -> str:
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
        return normalize_investigation_alert_text(str(args.get("alert_text", "")))
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
        # Investigation alert_text is freeform: a synthesized RCA varies per
        # live run, and a pasted payload may be forwarded verbatim or lightly
        # wrapped (CI 306 prefixed ``this alert: ``). Empty fixture content
        # asserts kind + non-empty text; a pinned payload must appear inside it.
        if expected_kind == "investigation":
            assert actual.get("kind") == "investigation"
            actual_content = str(actual.get("content", "")).strip()
            expected_content = str(expected.get("content", "")).strip()
            assert actual_content, f"investigation action {index} must include alert_text."
            if expected_content:
                assert expected_content in actual_content, (
                    f"investigation action {index} content {actual_content!r} "
                    f"does not contain {expected_content!r}"
                )
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


def _strip_redundant_integrations_list_for_investigation_plan(
    actual_actions: list[ExpectedAction],
    expected_actions: list[ExpectedAction],
) -> list[ExpectedAction]:
    """Drop a harmless ``/integrations list`` plan when dispatch is the sole expectation.

    Live planners occasionally emit this read-only slash before an
    ``investigation_start`` even when the fixture session already has connected
    integrations (see scenario 314). It does not change the turn outcome.
    """
    return normalize_executed_actions_for_oracle_match(
        actual_actions,
        expected_actions,
    )


def _planning_actions_for_match(
    actual_actions: list[ExpectedAction],
    expected_actions: list[ExpectedAction],
) -> list[ExpectedAction]:
    return _strip_redundant_integrations_list_for_investigation_plan(
        actual_actions,
        expected_actions,
    )


def test_planning_match_accepts_wrapped_investigation_payload() -> None:
    """Live planners may prefix a pasted JSON payload (CI 306)."""
    actual = cast(
        "list[ExpectedAction]",
        [
            {
                "kind": "investigation",
                "content": 'this alert: {"alertname": "HighCPU", "severity": "critical"}',
                "target_surface": "investigation",
            }
        ],
    )
    expected = cast(
        "list[ExpectedAction]",
        [
            {
                "kind": "investigation",
                "content": '{"alertname": "HighCPU", "severity": "critical"}',
                "target_surface": "investigation",
            }
        ],
    )
    _assert_planned_actions_match(actual, expected)


def _resolve_selected_cases(config: pytest.Config) -> list[ScenarioCase]:
    """Resolve which scenarios run, then shard them.

    The live suite is downsampled by default (everywhere, including CI) to a
    small representative subset. Selection precedence:

    * ``--turn-select=all`` / ``TURN_SELECT=all`` -> the FULL suite.
    * ``--turn-select=<mode>:<n>`` / ``TURN_SELECT`` -> complex/sample subset.
    * ``--turn-select=346,347`` / id list -> those scenario ids (or prefixes).
    * unset -> the default representative gate.

    The chosen set is then sharded via ``TURN_SHARD_TOTAL`` / ``TURN_SHARD_INDEX``
    so each CI piece stays small.
    """
    spec = config.getoption("--turn-select", default=None) or os.getenv("TURN_SELECT")
    seed_raw = config.getoption("--turn-select-seed", default=None) or os.getenv("TURN_SELECT_SEED")
    seed = int(str(seed_raw)) if seed_raw else 1337
    spec_text = str(spec) if spec else None

    if spec_text is None:
        selected = _DEFAULT_GATE_CASES
    elif is_full_selection(spec_text):
        selected = _ALL_CASES
    else:
        selected = select_cases(_ALL_CASES, spec=spec_text, seed=seed)
    return iter_scenarios_for_shard(selected)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    wants_planning = "live_planning_case" in metafunc.fixturenames
    wants_oracle = "live_oracle_case" in metafunc.fixturenames
    if not (wants_planning or wants_oracle):
        return
    selected = _resolve_selected_cases(metafunc.config)
    ids = [case.scenario.id for case in selected]
    if wants_planning:
        metafunc.parametrize("live_planning_case", selected, ids=ids)
    if wants_oracle:
        metafunc.parametrize("live_oracle_case", selected, ids=ids)


def test_shard_selection_is_non_empty() -> None:
    if _LIVE_CASES:
        return
    total, index = read_shard_config()
    skip_or_fail(f"No turn cases selected for shard {index}/{total}.")


def _assert_live_action_planning_once(case: ScenarioCase) -> None:
    from core.agent_harness.prompts.memory.conversation import expand_affirmative_follow_up
    from core.agent_harness.session.pending_offer import (
        first_pending_offer,
        parse_investigation_accept_message,
    )

    resolved_override, _unavailable = resolve_live_integrations(
        case.scenario.session.resolved_integrations
    )
    session = session_from_scenario(
        case.scenario.session,
        resolved_integrations_override=resolved_override,
        available_capabilities=session_capabilities(case.scenario.available_capabilities),
    )
    prompt = expand_affirmative_follow_up(
        case.scenario.input.prompt,
        session.cli_agent_messages,
        pending_offer=first_pending_offer(session),
    )
    answer = case.answer
    expected_actions = cast("list[ExpectedAction]", [dict(item) for item in answer.planned_actions])

    # Structured Want-me-to yes → /investigate alert:… (literal slash path).
    # Planning probes skip that driver, so assert the same deterministic slash.
    accept_alert = parse_investigation_accept_message(prompt)
    if accept_alert is not None:
        alert_arg = f"alert:{accept_alert}"
        actual_actions = [
            {
                "kind": "slash",
                "content": _slash_content("/investigate", [alert_arg]),
                "source": "deterministic",
                "target_surface": "slash",
                "command": "/investigate",
                "args": [alert_arg],
            }
        ]
        _assert_planned_actions_match(actual_actions, expected_actions)
        return

    ctx = ActionToolScope(
        session=session, console=Console(file=io.StringIO(), force_terminal=False)
    )
    tools = get_action_tools_from_integrations_view(ctx, resolved_integrations=resolved_override)
    from core.llm.factory import LLMRole, get_llm

    llm = get_llm(LLMRole.AGENT)
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot

    result = Agent(
        llm=llm,
        system=build_action_system_prompt(
            TurnSnapshot.from_session(prompt, session, surface="interactive_shell")
        ),
        tools=[_planning_probe_tool(tool) for tool in tools],
        resolved_integrations={},
        max_iterations=_LIVE_PLANNING_MAX_ITERATIONS,
    ).run([{"role": "user", "content": build_action_user_message(prompt)}])
    actions = _scenario_tool_calls([tool_call for tool_call, _output in result.executed])
    actual_actions = [_build_actual_action(action) for action in actions]
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
    runs = effective_runs(live_planning_case.answer.runs)
    failures: list[str] = []
    passed_count = 0

    for _ in range(runs):
        try:
            _assert_live_action_planning_once(live_planning_case)
        except LLMCreditExhaustedError as exc:
            _skip_or_fail_provider_credit_exhausted(str(exc))
        except RuntimeError as exc:
            msg = str(exc)
            if _provider_credit_exhausted_message(msg):
                _skip_or_fail_provider_credit_exhausted(msg)
            if _missing_llm_credentials_message(msg):
                _skip_or_fail_missing_llm_credentials(msg)
            raise
        except AssertionError as exc:
            failures.append(str(exc))
        except Exception as exc:
            # OpenAI/Anthropic SDK init errors (e.g. OpenAIError: Missing
            # credentials) are not AssertionError/RuntimeError subclasses.
            msg = str(exc)
            if _provider_credit_exhausted_message(msg):
                _skip_or_fail_provider_credit_exhausted(msg)
            if _missing_llm_credentials_message(msg):
                _skip_or_fail_missing_llm_credentials(msg)
            raise
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
    _skip_if_live_integrations_unavailable(live_oracle_case)
    runs = effective_runs(live_oracle_case.answer.runs)
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
    if any(
        _provider_credit_exhausted_message(str(details.get("response_normalized", "")))
        for details in failed_details
    ):
        _skip_or_fail_provider_credit_exhausted(f"scenario={live_oracle_case.scenario.id!r}")

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


def test_planning_match_strips_skill_view_prelude() -> None:
    """skill_view loads a playbook; fixtures pin the following scenario kind."""
    skill = ToolCall(id="t1", name=ActionToolName.SKILL_VIEW, input={"name": "github-cli"})
    cli = ToolCall(
        id="t2",
        name=ActionToolName.CLI_EXEC,
        input={"payload": "integrations verify --dry-run"},
    )
    expected = cast(
        "list[ExpectedAction]",
        [
            {
                "kind": "cli_command",
                "content": "integrations verify --dry-run",
                "source": "llm",
                "target_surface": "terminal",
                "payload": "integrations verify --dry-run",
            }
        ],
    )
    actual = [_build_actual_action(action) for action in _scenario_tool_calls([skill, cli])]
    _assert_planned_actions_match(actual, expected)


def test_unknown_action_tool_still_fails_planning_match() -> None:
    with pytest.raises(AssertionError, match="Unexpected action tool call"):
        _build_actual_action(ToolCall(id="t1", name="not_a_registered_action", input={}))


def test_planning_match_strips_redundant_integrations_list_for_investigation() -> None:
    investigation = {
        "kind": "investigation",
        "content": "Windows crash",
        "source": "llm",
        "target_surface": "investigation",
    }
    integrations_list = {
        "kind": "slash",
        "content": "/integrations list",
        "source": "llm",
        "target_surface": "slash",
        "command": "/integrations",
        "args": ["list"],
    }
    expected = [{"kind": "investigation", "source": "llm", "target_surface": "investigation"}]
    matched = _planning_actions_for_match([investigation, integrations_list], expected)
    assert matched == [investigation]


def test_oracle_match_collapses_duplicate_investigation_dispatch() -> None:
    from tests.core.agent._oracle_runtime import (
        normalize_executed_actions_for_oracle_match,
        normalize_history_for_oracle_match,
    )

    investigation = {
        "kind": "investigation",
        "content": "Windows crash across sentry, github, and posthog",
    }
    expected = [{"kind": "investigation"}]
    duplicated = [investigation, dict(investigation)]

    assert normalize_executed_actions_for_oracle_match(duplicated, expected) == [investigation]
    assert normalize_history_for_oracle_match(
        [
            {"type": "alert", "text_normalized": "windows crash", "ok": True},
            {"type": "alert", "text_normalized": "windows crash", "ok": True},
        ],
        expected,
    ) == [{"type": "alert", "text_normalized": "windows crash", "ok": True}]


def test_oracle_match_strips_session_goal_continuation_history() -> None:
    """Session-goal nudges must not fail planner-contract history expectations."""
    from tests.core.agent._oracle_runtime import normalize_history_for_oracle_match

    user_turn = {
        "type": "cli_agent",
        "text_normalized": "walk through this 5-item checklist",
        "ok": True,
    }
    continuation = {
        "type": "cli_agent",
        "text_normalized": (
            "[session_goal] continue the active goal without asking whether to continue. "
            "goal: walk through this 5-item checklist"
        ),
        "ok": True,
    }
    assert normalize_history_for_oracle_match(
        [user_turn, continuation, dict(continuation)],
        [],
    ) == [user_turn]
