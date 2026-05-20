"""Load routing scenario directories into typed fixtures for pytest."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from app.cli.interactive_shell.commands import SLASH_COMMANDS
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
        "compound",
        "remote",
        "follow_up",
        "non_actionable",
    }
)
RISK_LEVELS = frozenset({"low", "medium", "high"})
TIERS = frozenset({"critical", "full"})
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
    "compound": "compound",
    "remote": "remote",
    "follow_up": "follow_up",
    "non_actionable": "non_actionable",
}


@dataclass(frozen=True)
class ScenarioInput:
    prompt: str
    surface: str


@dataclass(frozen=True)
class ScenarioSession:
    has_prior_state: bool
    remote_connected: bool
    configured_integrations: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioCapabilities:
    slash_commands: tuple[str, ...]
    cli_commands: tuple[str, ...]
    synthetic_suites: tuple[str, ...]


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    intent_class: str
    risk_level: str
    input: ScenarioInput
    session: ScenarioSession
    available_capabilities: ScenarioCapabilities
    notes: tuple[str, ...]
    behavior_class: str
    scenario_dir: Path


@dataclass(frozen=True)
class AnswerRoute:
    expected_kind: str
    expected_signals: tuple[str, ...]
    expected_command_text: str | None


@dataclass(frozen=True)
class AnswerPolicy:
    should_execute: bool
    # Deprecated: use forbidden_actions in response_contract instead.
    has_unhandled_clause: bool
    fail_closed: bool


@dataclass(frozen=True)
class Answer:
    route: AnswerRoute
    policy: AnswerPolicy
    planned_actions: tuple[dict[str, Any], ...]
    executed_actions: tuple[dict[str, Any], ...]
    response_contract: dict[str, list[str]]
    history_expected: tuple[dict[str, Any], ...]
    tier: str
    runs: int


@dataclass(frozen=True)
class ScenarioCase:
    scenario: Scenario
    answer: Answer


def _require_mapping(raw: object, *, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        msg = f"{label} must be a mapping, got {type(raw).__name__}."
        raise ValueError(msg)
    return cast(dict[str, Any], raw)


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

    risk_level = str(data.get("risk_level", "")).strip()
    if risk_level not in RISK_LEVELS:
        msg = f"{scenario_path}: invalid risk_level {risk_level!r}."
        raise ValueError(msg)

    input_raw = _require_mapping(data.get("input"), label=f"{scenario_path} input")
    prompt = str(input_raw.get("prompt", "")).strip()
    if not prompt:
        msg = f"{scenario_path}: input.prompt must be non-empty."
        raise ValueError(msg)
    surface = str(input_raw.get("surface", "interactive_cli")).strip() or "interactive_cli"

    session_raw = _require_mapping(data.get("session"), label=f"{scenario_path} session")
    capabilities_raw = _require_mapping(
        data.get("available_capabilities", {}),
        label=f"{scenario_path} available_capabilities",
    )

    return Scenario(
        id=scenario_id,
        title=title,
        intent_class=intent_class,
        risk_level=risk_level,
        input=ScenarioInput(prompt=prompt, surface=surface),
        session=ScenarioSession(
            has_prior_state=bool(session_raw.get("has_prior_state", False)),
            remote_connected=bool(session_raw.get("remote_connected", False)),
            configured_integrations=_string_list(
                session_raw.get("configured_integrations"),
                label=f"{scenario_path} session.configured_integrations",
            ),
        ),
        available_capabilities=ScenarioCapabilities(
            slash_commands=_string_list(
                capabilities_raw.get("slash_commands"),
                label=f"{scenario_path} slash_commands",
            ),
            cli_commands=_string_list(
                capabilities_raw.get("cli_commands"),
                label=f"{scenario_path} cli_commands",
            ),
            synthetic_suites=_string_list(
                capabilities_raw.get("synthetic_suites"),
                label=f"{scenario_path} synthetic_suites",
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
    if expected_kind not in {"slash", "cli_agent"}:
        msg = f"{answer_path}: invalid route.expected_kind {expected_kind!r}."
        raise ValueError(msg)

    should_execute = bool(policy_raw.get("should_execute", False))
    has_unhandled_clause = bool(policy_raw.get("has_unhandled_clause", False))
    fail_closed = bool(policy_raw.get("fail_closed", False))

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

    if not should_execute and "$ /" not in must_not_contain:
        must_not_contain.append("$ /")

    if has_unhandled_clause and should_execute:
        msg = f"{answer_path}: has_unhandled_clause=true requires should_execute=false."
        raise ValueError(msg)
    if not should_execute and executed_actions:
        msg = f"{answer_path}: should_execute=false requires executed_actions=[]."
        raise ValueError(msg)

    tier = str(data.get("tier", "critical")).strip() or "critical"
    if tier not in TIERS:
        msg = f"{answer_path}: invalid tier {tier!r}."
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

    return Answer(
        route=AnswerRoute(
            expected_kind=expected_kind,
            expected_signals=_string_list(
                route_raw.get("expected_signals"),
                label=f"{answer_path} route.expected_signals",
            ),
            expected_command_text=expected_command_text,
        ),
        policy=AnswerPolicy(
            should_execute=should_execute,
            has_unhandled_clause=has_unhandled_clause,
            fail_closed=fail_closed,
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
        tier=tier,
        runs=runs,
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
