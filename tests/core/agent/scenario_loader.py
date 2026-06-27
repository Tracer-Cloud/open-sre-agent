"""Load turn scenario directories into typed fixtures for pytest."""

from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from interactive_shell.command_registry import SLASH_COMMANDS
from interactive_shell.tools.synthetic_tool import (
    list_rds_postgres_scenarios,
)
from tests.core.agent._planned_action import (
    default_target_surface,
)

TESTS_DIR = Path(__file__).resolve().parent
SCENARIOS_DIR = TESTS_DIR / "scenarios"

INTENT_CLASSES = frozenset(
    {
        "chat_handoff",
        "local_execution",
        "investigation",
        "complex_shell_prompts",
        "compound",
        "remote",
        "follow_up",
        "non_actionable",
    }
)
VALID_TOOL_ACTION_SURFACES = frozenset({"dispatch", "gather"})
VALID_GATHER_EXPECTS = frozenset(
    {
        "not_called",
        "called",
        "call_any",
        "valid_data",
        "valid_data_any",
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
    "chat_handoff": "chat_handoff",
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
class AnswerTurn:
    expected_kind: str


@dataclass(frozen=True)
class AnswerPolicy:
    """Execution expectation for the action-agent tool path only.

    ``executes_terminal_action`` is true when the turn is expected to run at
    least one shell action AgentTool -- a slash command, shell command, sample
    alert, investigation start, synthetic run, etc. It is false for
    conversational turns that answer in chat without executing a terminal
    action.

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
    * ``must_return_valid_data_any`` — at least one of these tool names must be
      invoked AND return valid data (same success criteria as
      ``must_return_valid_data``).

    For ``must_call_any``, ``must_call_all``, and ``must_not_call`` a tool counts
    as "called" when it appears in ``ToolLoopResult.executed`` regardless of
    whether the call succeeded. ``must_return_valid_data`` additionally inspects
    the tool's output and only counts a call that returned valid data.
    """

    must_call_any: tuple[str, ...]
    must_call_all: tuple[str, ...]
    must_not_call: tuple[str, ...]
    must_return_valid_data: tuple[str, ...]
    must_return_valid_data_any: tuple[str, ...]


@dataclass(frozen=True)
class Answer:
    """Expected behavior for one turn scenario.

    A turn can resolve down one of two independent execution paths, and these
    fields only describe the first:

    1. Action agent -> AgentTool execution (the "execution" path). Covered by
       ``policy.executes_terminal_action``, ``planned_actions``, and dispatch
       entries in ``tool_actions`` (``surface: dispatch``). An empty
       ``planned_actions`` means the action agent is expected to hand the turn to
       the conversational assistant (an ``assistant_handoff``), i.e. no terminal
       action runs.

    2. Conversational answer + ``gather_tool_evidence`` tool loop (the "chat"
       path). Assert gather behaviour via ``tool_actions`` entries with
       ``surface: gather`` and an ``expect`` mode (``not_called``, ``called``,
       ``valid_data``, etc.). ``response_contract`` still covers reply text.
    """

    turn: AnswerTurn
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


def _gather_tool_names(entry: dict[str, Any], *, label: str) -> tuple[str, ...]:
    tool = entry.get("tool")
    tools = entry.get("tools")
    if tool is not None and tools is not None:
        msg = f"{label}: set either 'tool' or 'tools', not both."
        raise ValueError(msg)
    if isinstance(tool, str) and tool.strip():
        return (tool.strip(),)
    if tools is not None:
        return _string_list(tools, label=f"{label}.tools")
    msg = f"{label}: gather action requires 'tool' or 'tools'."
    raise ValueError(msg)


def _parse_tool_actions(
    raw: object,
    *,
    label: str,
    scenario_id: str,
    executes_terminal_action: bool,
) -> tuple[tuple[dict[str, Any], ...], GatheredToolsContract | None]:
    """Parse unified ``tool_actions`` into dispatch + gather contract views.

    ``surface: dispatch`` entries become ``executed_actions`` shapes. ``surface:
    gather`` entries aggregate into a :class:`GatheredToolsContract` by
    ``expect`` mode.
    """
    if raw is None:
        return (), None
    if not isinstance(raw, list):
        msg = f"{label} must be a list, got {type(raw).__name__}."
        raise ValueError(msg)

    executed: list[dict[str, Any]] = []
    must_call_any: list[str] = []
    must_call_all: list[str] = []
    must_not_call: list[str] = []
    must_return_valid_data: list[str] = []
    must_return_valid_data_any: list[str] = []

    for index, item in enumerate(raw):
        entry_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            msg = f"{entry_label} must be a mapping."
            raise ValueError(msg)
        entry = cast(dict[str, Any], item)
        surface = str(entry.get("surface", "")).strip()
        if surface not in VALID_TOOL_ACTION_SURFACES:
            msg = (
                f"{entry_label}: surface must be one of "
                f"{sorted(VALID_TOOL_ACTION_SURFACES)!r}, got {surface!r}."
            )
            raise ValueError(msg)

        if surface == "dispatch":
            if "expect" in entry:
                msg = f"{entry_label}: dispatch actions must not set 'expect'."
                raise ValueError(msg)
            dispatch_action = {key: value for key, value in entry.items() if key != "surface"}
            validate_action_shape(
                dispatch_action,
                prefix=f"{scenario_id} tool_actions[{index}]",
                require_source=False,
            )
            executed.append(dispatch_action)
            continue

        expect = str(entry.get("expect", "")).strip()
        if expect not in VALID_GATHER_EXPECTS:
            msg = (
                f"{entry_label}: expect must be one of {sorted(VALID_GATHER_EXPECTS)!r}, "
                f"got {expect!r}."
            )
            raise ValueError(msg)
        tool_names = _gather_tool_names(entry, label=entry_label)
        if expect == "not_called":
            must_not_call.extend(tool_names)
        elif expect == "called":
            must_call_all.extend(tool_names)
        elif expect == "call_any":
            must_call_any.extend(tool_names)
        elif expect == "valid_data":
            must_return_valid_data.extend(tool_names)
        else:
            must_return_valid_data_any.extend(tool_names)

    if not executes_terminal_action and executed:
        msg = f"{label}: executes_terminal_action=false requires no dispatch tool_actions."
        raise ValueError(msg)

    contract = GatheredToolsContract(
        must_call_any=tuple(must_call_any),
        must_call_all=tuple(must_call_all),
        must_not_call=tuple(must_not_call),
        must_return_valid_data=tuple(must_return_valid_data),
        must_return_valid_data_any=tuple(must_return_valid_data_any),
    )
    if not (
        contract.must_call_any
        or contract.must_call_all
        or contract.must_not_call
        or contract.must_return_valid_data
        or contract.must_return_valid_data_any
    ):
        return tuple(executed), None
    return tuple(executed), contract


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

    turn_raw = _require_mapping(data.get("turn"), label=f"{answer_path} turn")
    policy_raw = _require_mapping(data.get("policy"), label=f"{answer_path} policy")
    response_raw = _require_mapping(
        data.get("response_contract", {}),
        label=f"{answer_path} response_contract",
    )
    history_raw = _require_mapping(data.get("history", {}), label=f"{answer_path} history")

    expected_kind = str(turn_raw.get("expected_kind", "")).strip()
    if expected_kind != "agent":
        msg = f"{answer_path}: invalid turn.expected_kind {expected_kind!r}."
        raise ValueError(msg)
    if "expected_signals" in turn_raw:
        msg = f"{answer_path}: turn.expected_signals was removed; drop it from the fixture."
        raise ValueError(msg)
    if "expected_command_text" in turn_raw:
        msg = (
            f"{answer_path}: turn.expected_command_text was removed along with the "
            "deterministic command-detection layer; drop it from the fixture."
        )
        raise ValueError(msg)

    for removed_key in ("should_execute", "has_unhandled_clause", "fail_closed"):
        if removed_key in policy_raw:
            msg = (
                f"{answer_path}: policy.{removed_key!r} was removed; "
                "use policy.executes_terminal_action instead."
            )
            raise ValueError(msg)
    executes_terminal_action = bool(policy_raw.get("executes_terminal_action", False))

    if "executed_actions" in data or "gathered_tools_contract" in data:
        msg = (
            f"{answer_path}: executed_actions and gathered_tools_contract were removed; "
            "use tool_actions with surface dispatch|gather instead."
        )
        raise ValueError(msg)

    planned_actions = tuple(
        _normalize_planned_action(dict(item))
        for item in _action_list(
            data.get("planned_actions"), label=f"{answer_path} planned_actions"
        )
    )

    for index, action in enumerate(planned_actions):
        validate_action_shape(
            action,
            prefix=f"{scenario_id} planned_actions[{index}]",
            require_source=True,
        )

    executed_actions, gathered_tools_contract = _parse_tool_actions(
        data.get("tool_actions"),
        label=f"{answer_path} tool_actions",
        scenario_id=scenario_id,
        executes_terminal_action=executes_terminal_action,
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

    runs_raw = data.get("runs", 1)
    runs = int(runs_raw) if isinstance(runs_raw, int | str) else 1
    if runs < 1:
        msg = f"{answer_path}: runs must be >= 1."
        raise ValueError(msg)

    history_expected = _action_list(
        history_raw.get("expected"),
        label=f"{answer_path} history.expected",
    )

    return Answer(
        turn=AnswerTurn(
            expected_kind=expected_kind,
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
    """Read TURN_SHARD_TOTAL and TURN_SHARD_INDEX from the environment."""
    total = int(os.getenv("TURN_SHARD_TOTAL", "1"))
    index = int(os.getenv("TURN_SHARD_INDEX", "0"))
    if total < 1:
        msg = "TURN_SHARD_TOTAL must be >= 1"
        raise ValueError(msg)
    if index < 0 or index >= total:
        msg = "TURN_SHARD_INDEX must satisfy 0 <= index < TURN_SHARD_TOTAL"
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


_INTENT_COMPLEXITY_WEIGHT: dict[str, float] = {
    "compound": 5.0,
    "complex_shell_prompts": 4.0,
    "remote": 4.0,
    "investigation": 3.0,
    "follow_up": 2.0,
    "local_execution": 1.5,
    "chat_handoff": 1.0,
    "non_actionable": 0.5,
}
_LIVE_INTEGRATION_SENTINEL = "@live"
_SELECT_MODES = frozenset({"sample", "complex"})
_DEFAULT_SELECT_FRACTION = 0.05

# Spec values that explicitly request the FULL suite (opt out of the default
# representative downsample). Accepted by ``--turn-select`` / ``TURN_SELECT``.
FULL_SELECT_SENTINELS = frozenset({"all", "full", "everything", "*"})

# Default gate: the live suite is downsampled everywhere (local AND CI) to a
# small, stratified, representative subset so a run stays fast and cheap. Pick
# this many of the most complex scenarios per behaviour class; classes with
# fewer scenarios contribute all of theirs. Override with ``TURN_SELECT=all``
# to run the complete suite, or ``--turn-select`` for a different subset.
DEFAULT_GATE_PER_CLASS = 2

# Env var capping the majority-vote ``runs`` of each scenario. Defaults to 1 so
# a downsampled run does a single LLM call per test; set ``TURN_MAX_RUNS=0`` (or
# ``all``/``off``) to honour each fixture's ``runs`` (CI keeps full majority
# voting this way).
_TURN_MAX_RUNS_ENV = "TURN_MAX_RUNS"
_DEFAULT_MAX_RUNS_CAP = 1
_UNCAPPED_RUNS_TOKENS = frozenset({"", "0", "all", "none", "off", "uncapped"})


def is_full_selection(spec: str | None) -> bool:
    """True when ``spec`` explicitly requests the full (non-downsampled) suite."""
    return spec is not None and spec.strip().lower() in FULL_SELECT_SENTINELS


def select_representative(
    cases: list[ScenarioCase],
    *,
    per_class: int = DEFAULT_GATE_PER_CLASS,
) -> list[ScenarioCase]:
    """Return a small, deterministic, behaviour-class-stratified subset.

    For each behaviour class, keep the ``per_class`` most complex scenarios
    (ties broken by id), so every intent class stays represented while the total
    stays tiny. Selected cases keep their original ordering for stable test ids.
    This is the default gate applied when no explicit selection is requested.
    """
    if per_class < 1:
        msg = "per_class must be >= 1"
        raise ValueError(msg)
    if not cases:
        return []
    by_class: dict[str, list[ScenarioCase]] = {}
    for case in cases:
        by_class.setdefault(case.scenario.behavior_class, []).append(case)
    chosen_ids: set[str] = set()
    for behavior_class in sorted(by_class):
        ranked = sorted(
            by_class[behavior_class],
            key=lambda case: (scenario_complexity(case), case.scenario.id),
        )
        for case in ranked[max(0, len(ranked) - per_class) :]:
            chosen_ids.add(case.scenario.id)
    return [case for case in cases if case.scenario.id in chosen_ids]


def max_runs_cap(value: str | None = None) -> int | None:
    """Resolve the majority-vote ``runs`` cap from ``TURN_MAX_RUNS``.

    Returns ``None`` when uncapped (honour each fixture's ``runs``) and a
    positive int otherwise. Defaults to ``_DEFAULT_MAX_RUNS_CAP`` (1) when the
    env var is unset, so downsampled runs do a single LLM call per test.
    """
    raw = value if value is not None else os.getenv(_TURN_MAX_RUNS_ENV)
    if raw is None:
        return _DEFAULT_MAX_RUNS_CAP
    text = raw.strip().lower()
    if text in _UNCAPPED_RUNS_TOKENS:
        return None
    parsed = int(text)
    if parsed < 1:
        return None
    return parsed


def effective_runs(answer_runs: int) -> int:
    """Apply the ``TURN_MAX_RUNS`` cap to a fixture's ``runs`` value."""
    base = max(1, answer_runs)
    cap = max_runs_cap()
    return base if cap is None else min(base, cap)


def scenario_complexity(case: ScenarioCase) -> float:
    """Heuristic difficulty/cost score for ranking live turn scenarios.

    Higher means "more worth running when you can only afford a few": multi-step
    plans, majority-vote ``runs`` (the dominant live cost), gather-loop tool
    contracts, real ``@live`` integration calls, and prior-state/long prompts all
    push the score up. Used by ``select_cases`` in ``complex`` mode.
    """
    scenario = case.scenario
    answer = case.answer
    score = _INTENT_COMPLEXITY_WEIGHT.get(scenario.intent_class, 1.0)
    score += 3.0 * len(answer.planned_actions)
    score += 2.0 * len(answer.executed_actions)
    # Majority-vote fixtures issue ``runs`` LLM calls per test in BOTH the
    # planning and oracle suites, so this is the single biggest time multiplier.
    score += 2.0 * max(0, answer.runs - 1)
    contract = answer.gathered_tools_contract
    if contract is not None:
        score += float(
            len(contract.must_call_any)
            + len(contract.must_call_all)
            + len(contract.must_not_call)
            + len(contract.must_return_valid_data)
            + len(contract.must_return_valid_data_any)
        )
    override = scenario.session.resolved_integrations or {}
    score += 2.0 * sum(
        1 for value in override.values() if str(value).strip() == _LIVE_INTEGRATION_SENTINEL
    )
    if scenario.session.has_prior_state:
        score += 1.0
    score += min(len(scenario.input.prompt) / 200.0, 2.0)
    return score


@dataclass(frozen=True)
class SelectionSpec:
    """Parsed ``--turn-select`` / ``TURN_SELECT`` request.

    Exactly one of ``count`` (absolute) or ``fraction`` (0 < f <= 1) is set.
    """

    mode: str
    fraction: float | None = None
    count: int | None = None


def parse_selection_spec(spec: str | None) -> SelectionSpec | None:
    """Parse a selection spec like ``complex:5``, ``sample:0.1``, or ``complex``.

    Returns ``None`` for an empty/unset spec (meaning "run everything"). The
    count component may be an absolute integer (``6``), a percentage (``5%``), or
    a fraction (``0.05``); a bare ``complex``/``sample`` defaults to 5%.
    """
    if spec is None:
        return None
    text = spec.strip().lower()
    if not text:
        return None
    mode, sep, raw = text.partition(":")
    mode = mode.strip()
    if mode not in _SELECT_MODES:
        msg = f"Invalid turn selection mode {mode!r}; expected one of {sorted(_SELECT_MODES)}."
        raise ValueError(msg)
    raw = raw.strip()
    if not sep or not raw:
        return SelectionSpec(mode=mode, fraction=_DEFAULT_SELECT_FRACTION)
    if raw.endswith("%"):
        percent = float(raw[:-1])
        if not 0.0 < percent <= 100.0:
            msg = f"Turn selection percentage must be in (0, 100]; got {raw!r}."
            raise ValueError(msg)
        return SelectionSpec(mode=mode, fraction=percent / 100.0)
    value = float(raw)
    if value <= 0.0:
        msg = f"Turn selection count must be positive; got {raw!r}."
        raise ValueError(msg)
    if value < 1.0:
        return SelectionSpec(mode=mode, fraction=value)
    return SelectionSpec(mode=mode, count=int(value))


def select_cases(
    cases: list[ScenarioCase],
    *,
    spec: str | SelectionSpec | None,
    seed: int = 1337,
) -> list[ScenarioCase]:
    """Return a subset of ``cases`` for fast local iteration.

    ``spec`` selects either the most complex cases (``complex:N``) or a random
    sample (``sample:N``). ``None``/empty returns every case (the default, so CI
    and the full local suite are unchanged). Selected cases keep their original
    ordering for stable, readable test ids.
    """
    parsed = spec if isinstance(spec, SelectionSpec) else parse_selection_spec(spec)
    if parsed is None or not cases:
        return list(cases)
    if parsed.count is not None:
        count = parsed.count
    else:
        fraction = parsed.fraction if parsed.fraction is not None else _DEFAULT_SELECT_FRACTION
        count = math.ceil(len(cases) * fraction)
    count = max(1, min(count, len(cases)))
    if parsed.mode == "complex":
        ranked = sorted(cases, key=lambda case: (scenario_complexity(case), case.scenario.id))
        chosen = ranked[len(ranked) - count :]
    else:
        chosen = random.Random(seed).sample(cases, count)
    order = {case.scenario.id: index for index, case in enumerate(cases)}
    return sorted(chosen, key=lambda case: order[case.scenario.id])


__all__ = [
    "Answer",
    "AnswerPolicy",
    "AnswerTurn",
    "GatheredToolsContract",
    "SCENARIOS_DIR",
    "Scenario",
    "ScenarioCapabilities",
    "ScenarioCase",
    "ScenarioInput",
    "ScenarioSession",
    "SelectionSpec",
    "DEFAULT_GATE_PER_CLASS",
    "FULL_SELECT_SENTINELS",
    "effective_runs",
    "is_full_selection",
    "load_all_scenarios",
    "load_scenario_case",
    "load_scenarios_for_class",
    "iter_scenarios_for_shard",
    "max_runs_cap",
    "parse_selection_spec",
    "read_shard_config",
    "scenario_complexity",
    "select_cases",
    "select_representative",
    "validate_action_shape",
]
