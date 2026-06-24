"""Runtime helpers for live routing turn-execution oracle tests."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pytest
from rich.console import Console

# Sentinel a fixture's ``resolved_integrations`` uses to request the REAL,
# live-resolved config for a service instead of a pinned fake one. The oracle
# replaces ``<service>: "@live"`` with the integration resolved from the local
# store / env (real credentials) and forces ``connection_verified: true`` so the
# tool is available. Scenarios that use it pair it with
# ``gathered_tools_contract.must_return_valid_data`` to assert the tool reached
# the live integration and returned valid data (not a 401). When the credential
# cannot be resolved the scenario is skipped, never failed (env gap, not bug).
LIVE_INTEGRATION_SENTINEL = "@live"

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
from app.cli.interactive_shell.routing.tests.scenario_loader import (
    GatheredToolsContract,
    ScenarioCapabilities,
    ScenarioCase,
)
from app.cli.interactive_shell.runtime.execution import execute_routed_turn
from app.cli.interactive_shell.runtime.session import ReplSession

# Sentinel a fixture's ``resolved_integrations`` uses to request the REAL,
# live-resolved config for a service instead of a pinned fake one. The oracle
# replaces ``<service>: "@live"`` with the integration resolved from the local
# store / env (real credentials) and forces ``connection_verified: true`` so the
# tool is available. Scenarios that use it pair it with
# ``gathered_tools_contract.must_return_valid_data`` to assert the tool reached
# the live integration and returned valid data (not a 401). When the credential
# cannot be resolved the scenario is skipped, never failed (env gap, not bug).
LIVE_INTEGRATION_SENTINEL = "@live"


@dataclass
class OracleRunResult:
    passed: bool
    details: dict[str, Any]


def resolve_live_integrations(
    override: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Expand any ``<service>: "@live"`` sentinel into a real resolved config.

    A fixture marks a service ``"@live"`` to opt into a real, credentialed call
    during the gather loop (see :data:`LIVE_INTEGRATION_SENTINEL`). For each such
    service this resolves the integration from the developer's local store / env
    (via the production ``resolve_integrations`` path) and forces
    ``connection_verified: true`` so the tool's ``is_available`` check passes —
    the local store omits that flag, but the live REPL sets it during startup, so
    the test mirrors the REPL rather than the bare classifier.

    Returns ``(expanded_override, unavailable_services)``. ``unavailable_services``
    lists services whose credentials could not be resolved; callers skip those
    scenarios rather than failing them (a missing credential is an environment
    gap, not a routing regression). Non-sentinel entries pass through untouched.
    """
    if not override:
        return override, []

    live_services = [
        service for service, config in override.items() if config == LIVE_INTEGRATION_SENTINEL
    ]
    if not live_services:
        return override, []

    from app.core.orchestration.node.resolve_integrations import resolve_integrations

    resolved = resolve_integrations({})  # type: ignore[arg-type]  # real store/env resolution
    expanded: dict[str, Any] = {}
    unavailable: list[str] = []
    for service, config in override.items():
        if config != LIVE_INTEGRATION_SENTINEL:
            expanded[service] = config
            continue
        live_config = resolved.get(service)
        # A usable integration must carry at least one credential token; the bare
        # classifier returns an empty shell for unconfigured services.
        if not isinstance(live_config, dict) or not any(
            live_config.get(field) for field in ("auth_token", "api_key", "api_token", "token")
        ):
            unavailable.append(service)
            continue
        expanded[service] = {**live_config, "connection_verified": True}
    return expanded, unavailable


def session_capabilities(capabilities: ScenarioCapabilities) -> dict[str, tuple[str, ...]]:
    """Project a scenario's three-state capabilities onto a session dict.

    Keys whose value is ``None`` (the capability is absent from the fixture) are
    omitted entirely so the tool stays available, mirroring the production
    default where ``ReplSession()`` carries no capability constraints. An
    explicit ``()`` (disabled) or a non-empty allowlist is passed through
    verbatim so the runtime capability gate sees the intended constraint.
    """
    projected: dict[str, tuple[str, ...]] = {}
    for key, value in (
        ("slash_commands", capabilities.slash_commands),
        ("cli_commands", capabilities.cli_commands),
        ("synthetic_suites", capabilities.synthetic_suites),
        ("llm_provider", capabilities.llm_provider),
    ):
        if value is not None:
            projected[key] = value
    return projected


def fresh_session(
    *,
    with_prior_state: bool,
    configured_integrations: tuple[str, ...] = (),
    available_capabilities: dict[str, tuple[str, ...]] | None = None,
    resolved_integrations_override: dict[str, Any] | None = None,
) -> ReplSession:
    session = ReplSession()
    if with_prior_state:
        session.last_state = {"root_cause": "disk full on orders-api"}
    session.configured_integrations = configured_integrations
    session.configured_integrations_known = True
    session.available_capabilities = available_capabilities or {}
    # When a scenario pins resolved_integrations, seed the gather-loop cache so
    # the conversational data-gathering pass sees a deterministic, fixture-owned
    # integration set instead of resolving the developer's real ~/.opensre store.
    # An explicit empty mapping ({}) deliberately forces a no-integration world.
    if resolved_integrations_override is not None:
        session.resolved_integrations_cache = resolved_integrations_override
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


def tool_output_returned_valid_data(output: Any) -> bool:
    """Whether a gathered tool's output is a successful integration response.

    The tool loop turns any tool exception (e.g. a Sentry 401 / 400) into
    ``{"error": ...}`` and read-only tools self-report ``available: false`` when
    they are not configured. A call returned valid data only when neither of
    those failure markers is present, i.e. the tool reached the live integration
    and got a real payload back. An empty-but-successful result (e.g. a 200 with
    zero matching issues) still counts: it is a valid integration response, not
    an auth/transport failure.
    """
    if isinstance(output, dict):
        if "error" in output:
            return False
        return output.get("available") is not False
    if isinstance(output, list):
        return True
    return output is not None


def _gathered_contract_failures(
    contract: GatheredToolsContract | None,
    gathered_tool_calls: list[str],
    gathered_valid_data: set[str],
) -> list[str]:
    """Return the names of any violated gathered-tools contract dimensions.

    For ``must_call_*`` / ``must_not_call`` a tool counts as "called" when it
    fired during the gather loop, regardless of whether it succeeded.
    ``must_return_valid_data`` is checked against ``gathered_valid_data`` — the
    set of tools that fired AND returned a successful integration response —
    so a credential/transport error fails the contract instead of passing as a
    bare "was called".
    """
    if contract is None:
        return []
    failures: list[str] = []
    called = set(gathered_tool_calls)
    if contract.must_call_any and not (called & set(contract.must_call_any)):
        failures.append("must_call_any")
    if any(name not in called for name in contract.must_call_all):
        failures.append("must_call_all")
    if any(name in called for name in contract.must_not_call):
        failures.append("must_not_call")
    if any(name not in gathered_valid_data for name in contract.must_return_valid_data):
        failures.append("must_return_valid_data")
    return failures


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
    resolved_override, _unavailable = resolve_live_integrations(
        case.scenario.session.resolved_integrations
    )
    session = fresh_session(
        with_prior_state=case.scenario.session.has_prior_state,
        configured_integrations=case.scenario.session.configured_integrations,
        available_capabilities=session_capabilities(case.scenario.available_capabilities),
        resolved_integrations_override=resolved_override,
    )
    executed: list[dict[str, Any]] = []
    patch_execution_boundary(monkeypatch, executed)

    # Record which registered tools fire during the conversational
    # gather_tool_evidence pass. gather_tool_evidence imports
    # run_tool_calling_loop lazily from app.core.runtime, so patch the name on
    # that source module (the local import re-binds from there at call time).
    import app.core.runtime as _runtime_mod

    gathered_tool_calls: list[str] = []
    gathered_valid_data: set[str] = set()
    _original_tool_loop = _runtime_mod.run_tool_calling_loop

    def _recording_tool_loop(*args: Any, **kwargs: Any) -> Any:
        result = _original_tool_loop(*args, **kwargs)
        for tc, output in result.executed:
            gathered_tool_calls.append(tc.name)
            if tool_output_returned_valid_data(output):
                gathered_valid_data.add(tc.name)
        return result

    monkeypatch.setattr(_runtime_mod, "run_tool_calling_loop", _recording_tool_loop)

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

    gathered_contract_failures = _gathered_contract_failures(
        answer.gathered_tools_contract, gathered_tool_calls, gathered_valid_data
    )

    passed = True
    if decision.route_kind.value != answer.route.expected_kind:
        passed = False
    if answer.policy.executes_terminal_action:
        if not executed_match:
            passed = False
    else:
        if executed:
            passed = False
        if normalize_response_text("$ /") in normalized_response:
            passed = False
    # Always enforce the response contract against actual runtime output;
    # there is no bypass for handoff-only runs. The oracle captures real console
    # output including any text printed by _execute_planned_actions, so
    # must_contain_any / must_contain_all must match what the runtime actually
    # emitted. (There is no planning-stage fail-closed denial in v0.1.)
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
    if gathered_contract_failures:
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
            "gathered_tool_calls": gathered_tool_calls,
            "gathered_valid_data": sorted(gathered_valid_data),
            "gathered_contract_failures": gathered_contract_failures,
            "last_assistant_intent": session.last_assistant_intent,
        },
    )
