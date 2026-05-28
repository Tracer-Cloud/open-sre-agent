"""Runtime helpers for live routing turn-execution oracle tests."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pytest
from rich.console import Console

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
    ACTION_KIND_TO_TOOL,
    REGISTRY,
)
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


def fresh_session(
    *,
    with_prior_state: bool,
    configured_integrations: tuple[str, ...] = (),
    available_capabilities: dict[str, tuple[str, ...]] | None = None,
) -> ReplSession:
    session = ReplSession()
    if with_prior_state:
        session.last_state = {"root_cause": "disk full on orders-api"}
    session.configured_integrations = configured_integrations
    session.configured_integrations_known = True
    session.available_capabilities = available_capabilities or {}
    return session


def match_actions(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> bool:
    if len(actual) != len(expected):
        return False
    return all(oracle_action_matches(item, expected[idx]) for idx, item in enumerate(actual))


def execution_expected_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: value
            for key, value in action.items()
            if key not in {"source", "target_surface", "content"}
        }
        for action in actions
    ]


def contains_any(haystack: str, needles: list[str]) -> bool:
    if not needles:
        return True
    normalized_needles = [normalize_response_text(needle) for needle in needles if needle.strip()]
    return any(needle in haystack for needle in normalized_needles)


def contains_all(haystack: str, needles: list[str]) -> bool:
    """True only when every needle appears in the haystack (or needles is empty)."""
    if not needles:
        return True
    normalized_needles = [normalize_response_text(needle) for needle in needles if needle.strip()]
    return all(needle in haystack for needle in normalized_needles)


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
    def _record_and_print(*, kind: str, action: dict[str, Any], ctx: Any) -> None:
        session = ctx.session
        console = ctx.console
        content = ""
        action_data = dict(action)
        action = {"kind": kind}
        if kind == "slash":
            command = str(action_data.get("command", "")).strip()
            raw_args = action_data.get("args")
            parsed_args = (
                [str(item).strip() for item in raw_args] if isinstance(raw_args, list) else []
            )
            action["command"] = command
            action["args"] = parsed_args
            content = " ".join([command, *parsed_args]) if parsed_args else command
            history_type = "slash"
        elif kind == "synthetic_test":
            suite = str(action_data.get("suite", "")).strip()
            scenario = str(action_data.get("scenario", "")).strip()
            action["suite"] = suite
            action["scenario"] = scenario
            content = f"{suite}:{scenario}"
            history_type = "synthetic_test"
        elif kind == "cli_command":
            payload = str(action_data.get("payload", "")).strip()
            action["payload"] = payload
            content = payload
            history_type = "cli_command"
        elif kind == "sample_alert":
            template = str(action_data.get("template", "")).strip()
            action["template"] = template
            content = template
            history_type = "alert"
        elif kind == "investigation":
            content = str(action_data.get("alert_text", "")).strip()
            action["content"] = content
            history_type = "alert"
        elif kind == "shell":
            content = str(action_data.get("command", "")).strip()
            action["content"] = content
            history_type = "shell"
        elif kind == "implementation":
            content = str(action_data.get("task", "")).strip()
            action["content"] = content
            history_type = "implementation"
        else:
            action["content"] = content
            history_type = "cli_agent"
        executed.append(action)
        session.record(history_type, content, ok=True)
        if kind == "slash":
            console.print(f"ran {content}")
        else:
            console.print(f"executed {kind}: {content}")

    tool_to_kind = {tool: kind for kind, tool in ACTION_KIND_TO_TOOL.items()}

    def _fake_dispatch(*, tool_name: str, args: dict[str, Any], ctx: Any) -> bool:
        kind = tool_to_kind.get(tool_name)
        if kind is None:
            return False
        if kind == "assistant_handoff":
            return True
        action_data = dict(args)
        _record_and_print(kind=kind, action=action_data, ctx=ctx)
        return True

    monkeypatch.setattr(REGISTRY, "dispatch", _fake_dispatch)


def run_oracle_once(case: ScenarioCase, monkeypatch: pytest.MonkeyPatch) -> OracleRunResult:
    session = fresh_session(
        with_prior_state=case.scenario.session.has_prior_state,
        configured_integrations=case.scenario.session.configured_integrations,
        available_capabilities={
            "slash_commands": case.scenario.available_capabilities.slash_commands,
            "cli_commands": case.scenario.available_capabilities.cli_commands,
            "synthetic_suites": case.scenario.available_capabilities.synthetic_suites,
        },
    )
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

    executed_expected = execution_expected_actions(
        [dict(action) for action in answer.executed_actions]
    )
    history_expected = [dict(item) for item in answer.history_expected]

    executed_match = match_actions(executed, executed_expected)
    history_match = history_matches(history_delta, history_expected)
    must_contain_any = answer.response_contract.get("must_contain_any", [])
    must_contain_all = answer.response_contract.get("must_contain_all", [])
    must_not_contain = answer.response_contract.get("must_not_contain", [])
    forbidden_action_kinds = answer.response_contract.get("forbidden_actions", [])

    any_match = contains_any(normalized_response, must_contain_any)
    all_match = contains_all(normalized_response, must_contain_all)
    forbidden_tokens = [
        token for token in must_not_contain if normalize_response_text(token) in normalized_response
    ]
    forbidden_executed = [
        action["kind"] for action in executed if action.get("kind") in forbidden_action_kinds
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
    # Always enforce the response contract against actual runtime output;
    # there is no bypass for handoff-only runs. The oracle captures real console
    # output including any text printed by _execute_planned_actions or
    # _render_plan_denied, so must_contain_any / must_contain_all must match
    # what the runtime actually emitted.
    if not any_match:
        passed = False
    if not all_match:
        passed = False
    if forbidden_tokens:
        passed = False
    if forbidden_executed:
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
            "forbidden_tokens_matched": forbidden_tokens,
            "forbidden_executed_kinds": forbidden_executed,
            "last_assistant_intent": session.last_assistant_intent,
        },
    )
