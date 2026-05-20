"""Live LLM action contracts for non-command routing prompts."""

from __future__ import annotations

from typing import NotRequired, TypedDict

import pytest
from pydantic import ValidationError

from app.cli.interactive_shell.commands import SLASH_COMMANDS
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm,
)
from app.cli.interactive_shell.routing.router import RouteKind, route_input
from app.cli.interactive_shell.routing.tests._dataset_schema import load_yaml_dataset
from app.cli.interactive_shell.runtime.session import ReplSession
from app.config import LLMSettings, get_configured_llm_provider, get_llm_provider_api_key_env

pytestmark = [pytest.mark.integration, pytest.mark.live_llm]


class ExpectedAction(TypedDict):
    kind: str
    content: str
    source: str
    target_surface: str
    command: NotRequired[str]
    args: NotRequired[list[str]]
    payload: NotRequired[str]
    suite: NotRequired[str]
    scenario: NotRequired[str]


class RouterActionContractCase(TypedDict):
    id: str
    input: str
    expected_kind: str
    expected_unhandled: bool
    expected_actions: list[ExpectedAction]
    with_prior_state: bool


_VALID_ROUTE_KINDS = {kind.value for kind in RouteKind}
_VALID_ACTION_KINDS = {
    "llm_provider",
    "slash",
    "shell",
    "sample_alert",
    "investigation",
    "synthetic_test",
    "task_cancel",
    "cli_command",
    "implementation",
}
_VALID_ACTION_SOURCES = {"deterministic", "llm"}
_VALID_TARGET_SURFACES = {"slash", "terminal", "investigation", "implementation"}


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
    return expected


@pytest.fixture(autouse=True)
def _require_default_llm_configuration() -> None:
    try:
        LLMSettings.from_env()
    except ValidationError as exc:
        provider = get_configured_llm_provider()
        env_var = get_llm_provider_api_key_env(provider)
        msg = exc.errors()[0].get("msg", str(exc)) if exc.errors() else str(exc)
        hint = f" configured provider={provider!r}"
        if env_var is not None:
            hint += f", required key={env_var}"
        pytest.fail(f"Live LLM action contracts require default LLM configuration:{hint}. {msg}")


def _load_action_cases(filename: str) -> list[RouterActionContractCase]:
    payload = load_yaml_dataset(filename)
    validated: list[RouterActionContractCase] = []

    for idx, row in enumerate(payload):
        case_id = str(row.get("id", "")).strip()
        if not case_id:
            msg = f"Fixture {filename} row {idx} has empty 'id'"
            raise ValueError(msg)

        text = str(row.get("input", ""))
        if not text.strip():
            msg = f"Fixture {filename} row {idx} has empty 'input'"
            raise ValueError(msg)

        expected_kind = str(row.get("expected_kind", "")).strip()
        if expected_kind not in _VALID_ROUTE_KINDS:
            msg = (
                f"Fixture {filename} row {idx} has invalid expected_kind "
                f"{expected_kind!r}."
            )
            raise ValueError(msg)

        expected_unhandled = row.get("expected_unhandled")
        if not isinstance(expected_unhandled, bool):
            msg = (
                f"Fixture {filename} row {idx} expected_unhandled must be a bool, "
                f"got {expected_unhandled!r}."
            )
            raise ValueError(msg)

        raw_actions = row.get("expected_actions")
        if not isinstance(raw_actions, list):
            msg = f"Fixture {filename} row {idx} expected_actions must be a list"
            raise ValueError(msg)

        actions: list[ExpectedAction] = []
        for action_idx, action in enumerate(raw_actions):
            if not isinstance(action, dict):
                msg = (
                    f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                    "must be a mapping"
                )
                raise ValueError(msg)

            kind = str(action.get("kind", "")).strip()
            content = str(action.get("content", "")).strip()
            source = str(action.get("source", "")).strip()
            target_surface = str(action.get("target_surface", "")).strip()
            if kind not in _VALID_ACTION_KINDS:
                msg = (
                    f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                    f"has invalid kind {kind!r}."
                )
                raise ValueError(msg)
            if not content:
                msg = (
                    f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                    "has empty content."
                )
                raise ValueError(msg)
            if source not in _VALID_ACTION_SOURCES:
                msg = (
                    f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                    f"has invalid source {source!r}."
                )
                raise ValueError(msg)
            if target_surface not in _VALID_TARGET_SURFACES:
                msg = (
                    f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                    f"has invalid target_surface {target_surface!r}."
                )
                raise ValueError(msg)

            expected_action: ExpectedAction = {
                "kind": kind,
                "content": content,
                "source": source,
                "target_surface": target_surface,
            }
            if kind == "slash":
                command = str(action.get("command", "")).strip()
                raw_args = action.get("args")
                if not command.startswith("/"):
                    msg = (
                        f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                        f"has invalid slash command {command!r}."
                    )
                    raise ValueError(msg)
                if command not in SLASH_COMMANDS:
                    msg = (
                        f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                        f"references unknown slash command {command!r}."
                    )
                    raise ValueError(msg)
                if not isinstance(raw_args, list) or not all(
                    isinstance(arg, str) and arg.strip() for arg in raw_args
                ):
                    msg = (
                        f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                        "must define non-empty string args list for slash actions."
                    )
                    raise ValueError(msg)
                args = [arg.strip() for arg in raw_args]
                if content != _slash_content(command, args):
                    msg = (
                        f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                        "content must match command+args for slash action."
                    )
                    raise ValueError(msg)
                expected_action["command"] = command
                expected_action["args"] = args
            elif kind == "cli_command":
                payload = str(action.get("payload", "")).strip()
                if not payload or payload != content:
                    msg = (
                        f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                        "must define payload equal to content for cli_command."
                    )
                    raise ValueError(msg)
                if payload.lower().startswith("opensre "):
                    msg = (
                        f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                        "cli_command payload must exclude the opensre prefix."
                    )
                    raise ValueError(msg)
                expected_action["payload"] = payload
            elif kind == "synthetic_test":
                suite = str(action.get("suite", "")).strip()
                scenario = str(action.get("scenario", "")).strip()
                if not suite or not scenario:
                    msg = (
                        f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                        "synthetic_test must define non-empty suite and scenario."
                    )
                    raise ValueError(msg)
                if content != f"{suite}:{scenario}":
                    msg = (
                        f"Fixture {filename} row {idx} expected_actions[{action_idx}] "
                        "content must match suite:scenario for synthetic_test."
                    )
                    raise ValueError(msg)
                expected_action["suite"] = suite
                expected_action["scenario"] = scenario

            actions.append(expected_action)

        validated.append(
            RouterActionContractCase(
                id=case_id,
                input=text,
                expected_kind=expected_kind,
                expected_unhandled=expected_unhandled,
                expected_actions=actions,
                with_prior_state=bool(row.get("with_prior_state", False)),
            )
        )

    return validated


@pytest.mark.parametrize(
    "case",
    _load_action_cases("router_action_contracts.yml"),
    ids=lambda case: case["id"],
)
def test_router_action_contracts(case: RouterActionContractCase) -> None:
    session = ReplSession()
    if case["with_prior_state"]:
        session.last_state = {"root_cause": "disk full on orders-api"}

    decision = route_input(case["input"], session)
    assert decision.route_kind.value == case["expected_kind"]

    llm_plan = plan_actions_with_llm(case["input"])
    assert llm_plan is not None, "Live LLM action planner did not return a parseable plan."
    actions, has_unhandled = llm_plan
    actual_actions = [_build_actual_action(action) for action in actions]

    assert actual_actions == case["expected_actions"]
    assert has_unhandled is case["expected_unhandled"]
