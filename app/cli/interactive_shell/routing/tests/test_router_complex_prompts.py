"""Complex prompt routing contracts with required action-plan assertions."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import pytest
import yaml

from app.cli.interactive_shell.orchestration.action_planner import plan_actions_with_unhandled
from app.cli.interactive_shell.routing.router import route_input
from app.cli.interactive_shell.runtime.session import ReplSession

TESTS_DIR = Path(__file__).resolve().parent


class RequiredAction(TypedDict):
    kind: str
    content: str


class RouterComplexPromptCase(TypedDict):
    id: str
    input: str
    expected_kind: str
    expected_unhandled_clause: bool
    required_actions: list[RequiredAction]


def _load_prompt_cases(filename: str) -> list[RouterComplexPromptCase]:
    payload = yaml.safe_load((TESTS_DIR / filename).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        msg = f"Fixture {filename} must contain a top-level YAML list"
        raise ValueError(msg)

    cases: list[RouterComplexPromptCase] = []
    for idx, row in enumerate(payload):
        if not isinstance(row, dict):
            msg = f"Fixture {filename} row {idx} must be a mapping"
            raise ValueError(msg)

        raw_required = row.get("required_actions", [])
        if not isinstance(raw_required, list):
            msg = f"Fixture {filename} row {idx} required_actions must be a list"
            raise ValueError(msg)

        required_actions: list[RequiredAction] = []
        for required_idx, action in enumerate(raw_required):
            if not isinstance(action, dict):
                msg = (
                    f"Fixture {filename} row {idx} required_actions[{required_idx}] "
                    "must be a mapping"
                )
                raise ValueError(msg)
            required_actions.append(
                RequiredAction(
                    kind=str(action["kind"]),
                    content=str(action["content"]),
                )
            )

        cases.append(
            RouterComplexPromptCase(
                id=str(row["id"]),
                input=str(row["input"]),
                expected_kind=str(row["expected_kind"]),
                expected_unhandled_clause=bool(row.get("expected_unhandled_clause", False)),
                required_actions=required_actions,
            )
        )
    return cases


@pytest.mark.parametrize(
    "case",
    _load_prompt_cases("router_complex_prompts.yml"),
    ids=lambda case: case["id"],
)
def test_router_complex_prompts_include_required_actions(case: RouterComplexPromptCase) -> None:
    session = ReplSession()

    decision = route_input(case["input"], session)
    assert decision.route_kind.value == case["expected_kind"]

    planned_actions, has_unhandled_clause = plan_actions_with_unhandled(case["input"])
    assert has_unhandled_clause is case["expected_unhandled_clause"]

    actual = [(action.kind, action.content) for action in planned_actions]
    expected = [(action["kind"], action["content"]) for action in case["required_actions"]]
    # Complex prompt contracts are strict: preserve exact sequence and no extras.
    assert actual == expected
