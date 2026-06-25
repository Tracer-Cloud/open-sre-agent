"""Load routing scenario directories into typed fixtures for pytest."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from app.cli.interactive_shell.command_registry import SLASH_COMMANDS
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    default_target_surface,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.synthetic_scenarios import (
    list_rds_postgres_scenarios,
)

TESTS_DIR = Path(__file__).resolve().parent
SCENARIOS_DIR = TESTS_DIR / "scenarios"

INTENT_CLASSES = frozenset(
    {
        "deterministic",
        "docs_no_execute",
        "local_execution",
        "investigation",
        "complex_shell_prompts",
        "compound",
        "remote",
        "follow_up",
        "non_actionable",
    }
)
VALID_ACTION_KINDS = frozenset(
    {
        "llm_provider",
        "slash",
        "shell",
        "sample_alert",
        "investigation",
        "synthetic_test",
        "task_cancel",
        "cli_command",
        "implementation",
        "assistant_handoff",
    }
)
VALID_ACTION_SOURCES = frozenset({"deterministic", "llm"})
VALID_TARGET_SURFACES = frozenset({"slash", "terminal", "investigation", "implementation"})

INTENT_TO_BEHAVIOR_CLASS: dict[str, str] = {
    "deterministic": "deterministic",
    "docs_no_execute": "docs_no_execute",
    "local_execution": "local_execution",
    "investigation": "investigations",
    "complex_shell_prompts": "complex_shell_prompts",
    "compound": "compound",
    "remote": "remote",
    "follow_up": "follow_up",
    "non_actionable": "non_actionable",
}


@dataclass(frozen=True)
class ScenarioInput:
    prompt: str


@dataclass(frozen=True)
class ScenarioSession:
    has_prior_state: bool
    configured_integrations: tuple[str, ...]
    resolved_integrations: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScenarioCapabilities:
    """Per-scenario planner capability constraints (three-state).

    Each field carries one of three states that map directly onto the runtime
    capability gate (``capability_not_explicitly_disabled``):

    * ``None`` — the capability key is absent; the tool stays available, which
      matches the production default (``ReplSession()`` has no capability
      constraints).
    * ``()`` — an explicit empty list; the tool is explicitly disabled (hidden
      from the planner specs and blocked at dispatch).
    * a non-empty tuple — an allowlist; the tool is available and the action
      normalizer drops proposed actions outside the list.
    """

    slash_commands: tuple[str, ...] | None
    cli_commands: tuple[str, ...] | None
    synthetic_suites: tuple[str, ...] | None
    llm_provider: tuple[str, ...] | None


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    intent_class: str
    input: ScenarioInput
    session: ScenarioSession
    available_capabilities: ScenarioCapabilities
    notes: tuple[str, ...]
    behavior_class: str
    scenario_dir: Path


@dataclass(frozen=True)
class AnswerRoute:
    expected_kind: str
    expected_command_text: str | None


@dataclass(frozen=True)
class AnswerPolicy:
    """Execution expectation for the planner -> dispatch path only.

    ``executes_terminal_action`` is true when the turn is expected to run at
    least one planned terminal action through the action-tool dispatch gate
    (``REGISTRY.dispatch``) -- a slash command, shell command, sample alert,
    investigation start, synthetic run, etc. It is false for conversational
    turns that answer in chat without dispatching a terminal action.

    This flag does NOT describe the conversational data-gathering path
    (``gather_tool_evidence``), where the assistant may query configured
    integrations (Sentry, GitHub, PostHog, ...) while composing a chat answer.
    That path is not modeled as planned/executed actions; it is asserted via
    ``response_contract`` text and by execution-layer tests. See the ``Answer``
    docstring for the full two-path model.
    """

    executes_terminal_action: bool


@dataclass(frozen=True)
class GatheredToolsContract:
    """Assertions on which registered tools fire during the conversational
    ``gather_tool_evidence`` loop for a turn.

    A turn's conversational data-gathering pass runs the same registered tools
    the investigation uses. This contract lets a scenario assert that the right
    tools were (or were not) invoked when grounding a chat answer:

    * ``must_call_any`` — at least one of these tool names must be invoked.
    * ``must_call_all`` — every one of these tool names must be invoked.
    * ``must_not_call`` — none of these tool names may be invoked.
    * ``must_return_valid_data`` — every one of these tool names must be invoked
      AND return a successful result (a real integration response, not an error
      or an ``available: false`` placeholder). This is strictly stronger than
      ``must_call_all``: it fails on a credential 401, a malformed-param 400, or
      any other errored call, so it can only pass when the tool actually reached
      the live integration and got valid data back.

    For ``must_call_any``, ``must_call_all``, and ``must_not_call`` a tool counts
    as "called" when it appears in ``ToolLoopResult.executed`` regardless of
    whether the call succeeded. ``must_return_valid_data`` additionally inspects
    the tool's output and only counts a call that returned valid data.
    """

    must_call_any: tuple[str, ...]
    must_call_all: tuple[str, ...]
    must_not_call: tuple[str, ...]
    must_return_valid_data: tuple[str, ...]


@dataclass(frozen=True)
class Answer:
    """Expected behavior for one routing scenario.

    A turn can resolve down one of two independent execution paths, and these
    fields only describe the first:

    1. Planner -> terminal action -> ``REGISTRY.dispatch`` (the "execution"
       path). Covered by ``policy.executes_terminal_action``,
       ``planned_actions``, and ``executed_actions``. An empty ``planned_actions``
       means the planner is expected to hand the turn to the conversational
       assistant (an ``assistant_handoff``), i.e. no terminal action runs.

    2. Conversational answer + ``gather_tool_evidence`` tool loop (the "chat"
       path). This is where the assistant answers in prose and may query
       configured integrations to ground that answer. It is NOT represented as
       planned/executed actions; the only assertions available here are the
       ``response_contract`` text checks. Deeper "did it actually query the
       integration?" assertions belong in execution-layer tests, not these
       routing fixtures.
    """

    route: AnswerRoute
    policy: AnswerPolicy
    planned_actions: tuple[dict[str, Any], ...]
    executed_actions: tuple[dict[str, Any], ...]
    response_contract: dict[str, list[str]]
    history_expected: tuple[dict[str, Any], ...]
    runs: int
    gathered_tools_contract: GatheredToolsContract | None = None


@dataclass(frozen=True)
class ScenarioCase:
    scenario: Scenario
    answer: Answer


def _require_mapping(raw: object, *, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        msg = f"{label} must be a mapping, got {type(raw).__name__}."
        raise ValueError(msg)
    return cast(dict[str, Any], raw)


def _optional_mapping(raw: object, *, label: str) -> dict[str, Any] | None:
    """Parse an optional mapping field.

    Returns ``None`` when the key is absent or explicitly null (preserving the
    "use the real resolved store" default), and the mapping itself when present
    (including an explicit empty ``{}`` that forces an isolated, empty store).
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        msg = f"{label} must be a mapping, got {type(raw).__name__}."
        raise ValueError(msg)
    return cast(dict[str, Any], raw)


def _parse_gathered_tools_contract(raw: object, *, label: str) -> GatheredToolsContract | None:
    """Parse the optional ``gathered_tools_contract`` block.

    Returns ``None`` when absent. Each of ``must_call_any``, ``must_call_all``,
    ``must_not_call``, and ``must_return_valid_data`` is an optional list of
    non-empty tool-name strings. Registry membership of those names is enforced
    separately by ``test_routing_fixture_integrity`` so the loader stays free of
    a heavy tool registry import.
    """
    if raw is None:
        return None
    mapping = _require_mapping(raw, label=label)
    contract = GatheredToolsContract(
        must_call_any=_string_list(mapping.get("must_call_any"), label=f"{label}.must_call_any"),
        must_call_all=_string_list(mapping.get("must_call_all"), label=f"{label}.must_call_all"),
        must_not_call=_string_list(mapping.get("must_not_call"), label=f"{label}.must_not_call"),
        must_return_valid_data=_string_list(
            mapping.get("must_return_valid_data"), label=f"{label}.must_return_valid_data"
        ),
    )
    if not (
        contract.must_call_any
        or contract.must_call_all
        or contract.must_not_call
        or contract.must_return_valid_data
    ):
        msg = (
            f"{label} must define at least one of "
            "must_call_any/must_call_all/must_not_call/must_return_valid_data."
        )
        raise ValueError(msg)
    return contract


def _string_list(raw: object, *, label: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        msg = f"{label} must be a list, got {type(raw).__name__}."
        raise ValueError(msg)
    values: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            msg = f"{label}[{index}] must be a non-empty string."
            raise ValueError(msg)
        values.append(item.strip())
    return tuple(values)


def _optional_string_list(raw: object, *, label: str) -> tuple[str, ...] | None:
    """Parse a capability allowlist while preserving the absent-vs-empty split.

    Returns ``None`` when the key is absent or explicitly null (no constraint;
    the tool stays available, matching the production default), ``()`` for an
    explicit empty list (the capability is explicitly disabled), and a tuple of
    non-empty strings for an allowlist.
    """
    if raw is None:
        return None
    return _string_list(raw, label=label)


def _action_list(raw: object, *, label: str) -> tuple[dict[str, Any], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        msg = f"{label} must be a list, got {type(raw).__name__}."
        raise ValueError(msg)
    actions: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            msg = f"{label}[{index}] must be a mapping."
            raise ValueError(msg)
        actions.append(cast(dict[str, Any], item))
    return tuple(actions)


def _slash_content(command: str, args: list[str]) -> str:
    return " ".join([command, *args]) if args else command


def _normalize_planned_action(action: dict[str, Any]) -> dict[str, Any]:
    """Backfill derived fields so YAMLs can omit redundant data."""
    kind = str(action.get("kind", "")).strip()
    if kind == "slash":
        command = str(action.get("command", "")).strip()
        raw_args = action.get("args") or []
        args = [str(arg).strip() for arg in raw_args] if isinstance(raw_args, list) else []
        if "content" not in action and command:
            action["content"] = _slash_content(command, args)
    elif kind == "synthetic_test":
        suite = str(action.get("suite", "")).strip()
        scenario = str(action.get("scenario", "")).strip()
        if "content" not in action and suite and scenario:
            action["content"] = f"{suite}:{scenario}"
    elif kind == "cli_command":
        payload = str(action.get("payload", "")).strip()
        if "content" not in action and payload:
            action["content"] = payload
    elif kind == "sample_alert":
        if "content" not in action and "template" in action:
            action["content"] = str(action["template"]).strip()
    return action


def validate_action_shape(
    action: dict[str, Any],
    *,
    prefix: str,
    require_source: bool,
) -> None:
    kind = str(action.get("kind", "")).strip()
    if kind not in VALID_ACTION_KINDS:
        msg = f"{prefix} has invalid kind {kind!r}."
        raise ValueError(msg)

    if require_source and kind != "assistant_handoff":
        source = str(action.get("source", "")).strip()
        if source not in VALID_ACTION_SOURCES:
            msg = f"{prefix} has invalid source {source!r}."
            raise ValueError(msg)
        target_surface = str(action.get("target_surface", "")).strip()
        if target_surface not in VALID_TARGET_SURFACES:
            msg = f"{prefix} has invalid target_surface {target_surface!r}."
            raise ValueError(msg)
        canonical = default_target_surface(kind)  # type: ignore[arg-type]
        if target_surface != canonical:
            msg = (
                f"{prefix} target_surface {target_surface!r} "
                f"must be {canonical!r} for kind {kind!r}."
            )
            raise ValueError(msg)

    if kind == "slash":
        command = str(action.get("command", "")).strip()
        raw_args = action.get("args")
        if not command.startswith("/"):
            msg = f"{prefix} slash command must start with '/'."
            raise ValueError(msg)
        source = str(action.get("source", "")).strip()
        if require_source and source == "llm" and command not in SLASH_COMMANDS:
            msg = f"{prefix} references unknown slash command {command!r}."
            raise ValueError(msg)
        if not isinstance(raw_args, list):
            msg = f"{prefix} slash action must define args list."
            raise ValueError(msg)
        args = [str(arg).strip() for arg in raw_args]
        content = str(action.get("content", "")).strip()
        if content and content != _slash_content(command, args):
            msg = f"{prefix} content must match command+args when set."
            raise ValueError(msg)
    elif kind == "synthetic_test":
        suite = str(action.get("suite", "")).strip()
        scenario = str(action.get("scenario", "")).strip()
        if not suite or not scenario:
            msg = f"{prefix} synthetic_test requires suite and scenario."
            raise ValueError(msg)
        available = set(list_rds_postgres_scenarios())
        if scenario not in available:
            msg = f"{prefix} unknown synthetic scenario {scenario!r}."
            raise ValueError(msg)
        content = str(action.get("content", "")).strip()
        if content and content != f"{suite}:{scenario}":
            msg = f"{prefix} content must match suite:scenario when set."
            raise ValueError(msg)
    elif kind == "cli_command":
        payload = str(action.get("payload", "")).strip()
        if not payload:
            msg = f"{prefix} cli_command requires payload."
            raise ValueError(msg)
        if payload.lower().startswith("opensre "):
            msg = f"{prefix} cli_command payload must not include opensre prefix."
            raise ValueError(msg)


def _parse_scenario_yaml(
    scenario_path: Path,
    *,
    behavior_class: str,
) -> Scenario:
    raw = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    data = _require_mapping(raw, label=str(scenario_path))

    scenario_id = str(data.get("id", "")).strip()
    if not scenario_id:
        msg = f"{scenario_path}: missing id."
        raise ValueError(msg)

    title = str(data.get("title", "")).strip()
    if not title:
        msg = f"{scenario_path}: missing title."
        raise ValueError(msg)

    intent_class = str(data.get("intent_class", "")).strip()
    if intent_class not in INTENT_CLASSES:
        msg = f"{scenario_path}: invalid intent_class {intent_class!r}."
        raise ValueError(msg)

    expected_behavior = INTENT_TO_BEHAVIOR_CLASS.get(intent_class)
    if expected_behavior != behavior_class:
        msg = (
            f"{scenario_path}: intent_class {intent_class!r} "
            f"does not match directory behavior class {behavior_class!r}."
        )
        raise ValueError(msg)

    input_raw = _require_mapping(data.get("input"), label=f"{scenario_path} input")
    prompt = str(input_raw.get("prompt", "")).strip()
    if not prompt:
        msg = f"{scenario_path}: input.prompt must be non-empty."
        raise ValueError(msg)

    session_raw = _require_mapping(data.get("session"), label=f"{scenario_path} session")
    capabilities_raw = _require_mapping(
        data.get("available_capabilities", {}),
        label=f"{scenario_path} available_capabilities",
    )

    return Scenario(
        id=scenario_id,
        title=title,
        intent_class=intent_class,
        input=ScenarioInput(prompt=prompt),
        session=ScenarioSession(
            has_prior_state=bool(session_raw.get("has_prior_state", False)),
            configured_integrations=_string_list(
                session_raw.get("configured_integrations"),
                label=f"{scenario_path} session.configured_integrations",
            ),
            resolved_integrations=_optional_mapping(
                session_raw.get("resolved_integrations"),
                label=f"{scenario_path} session.resolved_integrations",
            ),
        ),
        available_capabilities=ScenarioCapabilities(
            slash_commands=_optional_string_list(
                capabilities_raw.get("slash_commands"),
                label=f"{scenario_path} slash_commands",
            ),
            cli_commands=_optional_string_list(
                capabilities_raw.get("cli_commands"),
                label=f"{scenario_path} cli_commands",
            ),
            synthetic_suites=_optional_string_list(
                capabilities_raw.get("synthetic_suites"),
                label=f"{scenario_path} synthetic_suites",
            ),
            llm_provider=_optional_string_list(
                capabilities_raw.get("llm_provider"),
                label=f"{scenario_path} llm_provider",
            ),
        ),
        notes=_string_list(data.get("notes"), label=f"{scenario_path} notes"),
        behavior_class=behavior_class,
        scenario_dir=scenario_path,
    )


def _parse_answer_yaml(answer_path: Path, *, scenario_id: str) -> Answer:
    raw = yaml.safe_load(answer_path.read_text(encoding="utf-8"))
    data = _require_mapping(raw, label=str(answer_path))

    route_raw = _require_mapping(data.get("route"), label=f"{answer_path} route")
    policy_raw = _require_mapping(data.get("policy"), label=f"{answer_path} policy")
    response_raw = _require_mapping(
        data.get("response_contract", {}),
        label=f"{answer_path} response_contract",
    )
    history_raw = _require_mapping(data.get("history", {}), label=f"{answer_path} history")

    expected_kind = str(route_raw.get("expected_kind", "")).strip()
    if expected_kind != "handle_message_with_agent":
        msg = f"{answer_path}: invalid route.expected_kind {expected_kind!r}."
        raise ValueError(msg)
    if "expected_signals" in route_raw:
        msg = f"{answer_path}: route.expected_signals was removed; drop it from the fixture."
        raise ValueError(msg)

    for removed_key in ("should_execute", "has_unhandled_clause", "fail_closed"):
        if removed_key in policy_raw:
            msg = (
                f"{answer_path}: policy.{removed_key!r} was removed; "
                "use policy.executes_terminal_action instead."
            )
            raise ValueError(msg)
    executes_terminal_action = bool(policy_raw.get("executes_terminal_action", False))

    planned_actions = tuple(
        _normalize_planned_action(dict(item))
        for item in _action_list(
            data.get("planned_actions"), label=f"{answer_path} planned_actions"
        )
    )
    executed_actions = _action_list(
        data.get("executed_actions"),
        label=f"{answer_path} executed_actions",
    )

    for index, action in enumerate(planned_actions):
        validate_action_shape(
            action,
            prefix=f"{scenario_id} planned_actions[{index}]",
            require_source=True,
        )
    for index, action in enumerate(executed_actions):
        validate_action_shape(
            action,
            prefix=f"{scenario_id} executed_actions[{index}]",
            require_source=False,
        )

    must_contain_any = list(
        _string_list(
            response_raw.get("must_contain_any", response_raw.get("any_of_contains")),
            label=f"{answer_path} response_contract.must_contain_any",
        )
    )
    must_contain_all = list(
        _string_list(
            response_raw.get("must_contain_all"),
            label=f"{answer_path} response_contract.must_contain_all",
        )
    )
    must_not_contain = list(
        _string_list(
            response_raw.get("must_not_contain"),
            label=f"{answer_path} response_contract.must_not_contain",
        )
    )
    forbidden_actions = list(
        _string_list(
            response_raw.get("forbidden_actions"),
            label=f"{answer_path} response_contract.forbidden_actions",
        )
    )
    # Validate that forbidden_actions entries reference known action kinds.
    for entry in forbidden_actions:
        if entry not in VALID_ACTION_KINDS:
            msg = f"{answer_path}: forbidden_actions entry {entry!r} is not a valid action kind."
            raise ValueError(msg)

    if not executes_terminal_action and "$ /" not in must_not_contain:
        must_not_contain.append("$ /")

    if not executes_terminal_action and executed_actions:
        msg = f"{answer_path}: executes_terminal_action=false requires executed_actions=[]."
        raise ValueError(msg)

    runs_raw = data.get("runs", 1)
    runs = int(runs_raw) if isinstance(runs_raw, int | str) else 1
    if runs < 1:
        msg = f"{answer_path}: runs must be >= 1."
        raise ValueError(msg)

    history_expected = _action_list(
        history_raw.get("expected"),
        label=f"{answer_path} history.expected",
    )

    command_text = route_raw.get("expected_command_text")
    expected_command_text = (
        str(command_text).strip()
        if isinstance(command_text, str) and command_text.strip()
        else None
    )

    gathered_tools_contract = _parse_gathered_tools_contract(
        data.get("gathered_tools_contract"),
        label=f"{answer_path} gathered_tools_contract",
    )

    return Answer(
        route=AnswerRoute(
            expected_kind=expected_kind,
            expected_command_text=expected_command_text,
        ),
        policy=AnswerPolicy(
            executes_terminal_action=executes_terminal_action,
        ),
        planned_actions=planned_actions,
        executed_actions=executed_actions,
        response_contract={
            "must_contain_any": must_contain_any,
            "must_contain_all": must_contain_all,
            "must_not_contain": must_not_contain,
            "forbidden_actions": forbidden_actions,
        },
        history_expected=history_expected,
        runs=runs,
        gathered_tools_contract=gathered_tools_contract,
    )


def load_scenario_case(scenario_file: Path, *, behavior_class: str) -> ScenarioCase:
    """Load one scenario file into a ScenarioCase."""
    if not scenario_file.is_file():
        msg = f"Missing scenario file: {scenario_file}"
        raise FileNotFoundError(msg)

    scenario = _parse_scenario_yaml(scenario_file, behavior_class=behavior_class)
    if scenario.scenario_dir.stem != scenario.id:
        msg = (
            f"{scenario_file}: file stem {scenario.scenario_dir.stem!r} "
            f"does not match scenario id {scenario.id!r}."
        )
        raise ValueError(msg)

    answer = _parse_answer_yaml(scenario_file, scenario_id=scenario.id)
    return ScenarioCase(scenario=scenario, answer=answer)


def load_all_scenarios() -> list[ScenarioCase]:
    """Discover and load every scenario under scenarios/<behavior_class>/*.yml."""
    if not SCENARIOS_DIR.is_dir():
        return []

    cases: list[ScenarioCase] = []
    seen_ids: set[str] = set()

    for behavior_dir in sorted(SCENARIOS_DIR.iterdir()):
        if not behavior_dir.is_dir():
            continue
        behavior_class = behavior_dir.name
        for scenario_file in sorted(behavior_dir.iterdir()):
            if not scenario_file.is_file() or scenario_file.suffix != ".yml":
                continue
            case = load_scenario_case(scenario_file, behavior_class=behavior_class)
            if case.scenario.id in seen_ids:
                msg = f"Duplicate scenario id {case.scenario.id!r}."
                raise ValueError(msg)
            seen_ids.add(case.scenario.id)
            cases.append(case)

    return cases


def load_scenarios_for_class(behavior_class: str) -> list[ScenarioCase]:
    """Load scenarios for one behavior-class directory."""
    return [case for case in load_all_scenarios() if case.scenario.behavior_class == behavior_class]


def read_shard_config() -> tuple[int, int]:
    """Read ROUTING_SHARD_TOTAL and ROUTING_SHARD_INDEX from the environment."""
    total = int(os.getenv("ROUTING_SHARD_TOTAL", "1"))
    index = int(os.getenv("ROUTING_SHARD_INDEX", "0"))
    if total < 1:
        msg = "ROUTING_SHARD_TOTAL must be >= 1"
        raise ValueError(msg)
    if index < 0 or index >= total:
        msg = "ROUTING_SHARD_INDEX must satisfy 0 <= index < ROUTING_SHARD_TOTAL"
        raise ValueError(msg)
    return total, index


def iter_scenarios_for_shard(
    cases: list[ScenarioCase],
    *,
    total: int | None = None,
    index: int | None = None,
) -> list[ScenarioCase]:
    """Return the shard subset of cases using stable offset modulo sharding."""
    shard_total, shard_index = (
        (total, index) if total is not None and index is not None else read_shard_config()
    )
    return [case for offset, case in enumerate(cases) if offset % shard_total == shard_index]


__all__ = [
    "Answer",
    "AnswerPolicy",
    "AnswerRoute",
    "GatheredToolsContract",
    "SCENARIOS_DIR",
    "Scenario",
    "ScenarioCapabilities",
    "ScenarioCase",
    "ScenarioInput",
    "ScenarioSession",
    "load_all_scenarios",
    "load_scenario_case",
    "load_scenarios_for_class",
    "iter_scenarios_for_shard",
    "read_shard_config",
    "validate_action_shape",
]
