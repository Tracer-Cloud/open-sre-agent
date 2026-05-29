"""Contract and invariant tests for routing policy mapping and postprocessing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict, cast

import yaml

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    _finalize_planner_result_with_trace,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.deterministic_action_mapper import (
    map_actions_with_trace,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.mapper_runner import (
    RULE_PRECEDENCE,
)


class MapperContract(TypedDict):
    prompt: str
    has_unhandled: bool
    actions: list[dict[str, Any]]
    trace: list[dict[str, Any]]


class PostprocessingContract(TypedDict):
    id: str
    message: str
    input_has_unhandled: bool
    input_actions: list[dict[str, Any]]
    output_has_unhandled: bool
    actions: list[dict[str, Any]]
    trace: list[str]


def _contracts_path() -> Path:
    return Path(__file__).resolve().parent / "contracts" / "policy_contracts.yml"


def _load_contracts() -> dict[str, Any]:
    raw = yaml.safe_load(_contracts_path().read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return cast(dict[str, Any], raw)


def _action_view(actions: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "kind": action.kind,
            "content": action.content,
            "source": action.source,
            "target_surface": action.target_surface,
        }
        for action in actions
    ]


def test_mapper_contracts_are_stable() -> None:
    contracts = cast(list[MapperContract], _load_contracts()["mapper_contracts"])
    for contract in contracts:
        actions, has_unhandled, trace = map_actions_with_trace(contract["prompt"])
        assert has_unhandled == contract["has_unhandled"]
        assert _action_view(actions) == contract["actions"]
        assert trace == contract["trace"]


def test_postprocessing_contracts_are_stable() -> None:
    contracts = cast(list[PostprocessingContract], _load_contracts()["postprocessing_contracts"])
    for contract in contracts:
        actions, has_unhandled, trace = _finalize_planner_result_with_trace(
            contract["message"],
            _actions_from_contract(contract["input_actions"]),
            contract["input_has_unhandled"],
        )
        assert has_unhandled == contract["output_has_unhandled"]
        assert _action_view(actions) == contract["actions"]
        assert list(trace) == contract["trace"]


def _actions_from_contract(actions: list[dict[str, Any]]) -> list[Any]:
    from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
        ActionKind,
        ActionSource,
        PlannedAction,
    )

    return [
        PlannedAction(
            kind=cast(ActionKind, str(action["kind"])),
            content=str(action.get("content", "")),
            position=0,
            source=cast(ActionSource, str(action.get("source", "llm"))),
            target_surface=action.get("target_surface"),
        )
        for action in actions
    ]


def test_invariant_actions_are_sorted_by_position() -> None:
    actions, has_unhandled, _trace = map_actions_with_trace(
        'run /remote and then investigate "hello world"'
    )
    assert has_unhandled is False
    positions = [action.position for action in actions]
    assert positions == sorted(positions)


def test_invariant_duplicate_slash_actions_are_deduped() -> None:
    actions, has_unhandled, _trace = map_actions_with_trace(
        "check opensre health and then check opensre health"
    )
    # The second duplicated clause is intentionally left unmatched after dedupe,
    # so the mapper fail-closes by surfacing an unhandled clause.
    assert has_unhandled is True
    slash_actions = [action.content for action in actions if action.kind == "slash"]
    assert slash_actions == ["/health"]


def test_invariant_fail_closed_vague_local_model() -> None:
    actions, has_unhandled, trace = _finalize_planner_result_with_trace(
        "please connect to local llama",
        [],
        False,
    )
    assert actions == []
    assert has_unhandled is True
    assert trace == ("fail_closed_vague_local_model",)


def test_mapper_precedence_table_is_explicit_and_stable() -> None:
    assert [rule.name for rule in RULE_PRECEDENCE] == [
        "synthetic_suite",
        "registry_commands",
        "integration_details",
        "fallback_provider_switch",
        "fallback_sample_alert",
        "fallback_investigation",
        "fallback_implementation",
        "fallback_task_cancel",
        "fallback_shell",
    ]
