"""Live LLM contracts for the structured action planner."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

import pytest
import yaml
from pydantic import ValidationError

from app.cli.interactive_shell.routing.handle_message_with_agent.command_dispatch import (
    deterministic_command_text,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm,
)
from app.cli.interactive_shell.routing.router import route_input
from app.cli.interactive_shell.runtime.session import ReplSession
from app.config import (
    DEFAULT_LLM_RESOLUTION_FALLBACK_PROVIDERS,
    get_configured_llm_provider,
    get_llm_provider_api_key_env,
    resolve_llm_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
PROMPT_TURN_CONTRACTS_DATASET = (
    PROJECT_ROOT / "app/cli/interactive_shell/routing/tests/prompt_turn_contracts.yml"
)

pytestmark = [pytest.mark.integration, pytest.mark.live_llm]


class ExpectedAction(TypedDict):
    kind: str
    content: str


class PlannerLiveCase(TypedDict):
    id: str
    input: str
    expected_kind: str
    expected_actions: list[ExpectedAction]


def _load_live_cases() -> list[PlannerLiveCase]:
    payload = yaml.safe_load(PROMPT_TURN_CONTRACTS_DATASET.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        msg = f"{PROMPT_TURN_CONTRACTS_DATASET} must contain a top-level YAML list"
        raise ValueError(msg)

    cases: list[PlannerLiveCase] = []
    for idx, row in enumerate(payload):
        if not isinstance(row, dict):
            msg = f"{PROMPT_TURN_CONTRACTS_DATASET} row {idx} must be a mapping"
            raise ValueError(msg)

        raw_actions = row.get("expected_planned_actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            msg = (
                f"{PROMPT_TURN_CONTRACTS_DATASET} row {idx} must define "
                "non-empty expected_planned_actions"
            )
            raise ValueError(msg)

        cases.append(
            {
                "id": str(row["id"]),
                "input": str(row["input"]),
                "expected_kind": str(row["expected_route_kind"]),
                "expected_actions": [
                    {"kind": str(action["kind"]), "content": str(action["content"])}
                    for action in raw_actions
                    if isinstance(action, dict)
                ],
            }
        )
    return cases


@pytest.fixture(autouse=True)
def _require_default_llm_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        settings = resolve_llm_settings()
    except ValidationError as exc:
        provider = get_configured_llm_provider()
        env_var = get_llm_provider_api_key_env(provider)
        msg = exc.errors()[0].get("msg", str(exc)) if exc.errors() else str(exc)

        hint = f" configured provider={provider!r}"
        if env_var is not None:
            hint += f", required key={env_var}"

        hint += f", fallback providers={DEFAULT_LLM_RESOLUTION_FALLBACK_PROVIDERS!r}"

        pytest.skip(
            f"Skipping live LLM planner tests; missing usable LLM configuration:{hint}. {msg}"
        )

    from app.services.llm_client import reset_llm_singletons

    monkeypatch.setenv("LLM_PROVIDER", settings.provider)
    reset_llm_singletons()


def _compact_action(action: PlannedAction) -> ExpectedAction:
    return {"kind": action.kind, "content": action.content}


def _actions_for_case(case: PlannerLiveCase) -> list[ExpectedAction]:
    command_text = deterministic_command_text(case["input"])
    if command_text is not None:
        return [{"kind": "slash", "content": command_text}]

    llm_plan = plan_actions_with_llm(case["input"])
    if llm_plan is None:
        return []
    actions, has_unhandled = llm_plan
    if actions:
        return [_compact_action(action) for action in actions]

    assert has_unhandled is True
    assert case["expected_actions"] == [
        action for action in case["expected_actions"] if action["kind"] == "assistant_handoff"
    ]
    return case["expected_actions"]


def _is_transient_llm_provider_failure(records: list[logging.LogRecord]) -> bool:
    text = "\n".join(
        record.getMessage()
        for record in records
        if record.name.endswith("orchestration.llm_action_planner.llm_client")
    ).lower()
    return any(
        token in text
        for token in (
            "usage limit",
            "rate limit",
            "quota",
            "billing",
            "temporarily unavailable",
            "service unavailable",
        )
    )


def _normalize_for_assertion(actions: list[ExpectedAction]) -> list[ExpectedAction]:
    """Drop free-form ``content`` from ``assistant_handoff`` entries.

    Fixture content for handoffs encodes a *category slug* (e.g.
    ``docs:run_investigation``) describing the intent. The LLM tool-call
    planner emits free-form prose for the handoff body, which varies
    per-run. The behavioral contract that matters here is "the LLM
    correctly classified this prompt as a handoff (no executable
    action)" — not the specific text it would forward. Comparing
    kind-only for handoffs preserves the contract without forcing the
    LLM to reproduce arbitrary fixture strings.
    """
    normalized: list[ExpectedAction] = []
    for action in actions:
        if action["kind"] == "assistant_handoff":
            normalized.append({"kind": "assistant_handoff", "content": ""})
        else:
            normalized.append(action)
    return normalized


@pytest.mark.parametrize("case", _load_live_cases(), ids=lambda case: case["id"])
def test_live_llm_planner_matches_prompt_contract(
    case: PlannerLiveCase,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert route_input(case["input"], ReplSession()).route_kind.value == case["expected_kind"]
    caplog.clear()
    actual = _normalize_for_assertion(_actions_for_case(case))
    if not actual and _is_transient_llm_provider_failure(caplog.records):
        pytest.skip("Skipping live LLM planner case due to transient provider/billing limits.")
    if not actual:
        pytest.fail("Live LLM action planner did not return a parseable plan.")
    expected = _normalize_for_assertion(case["expected_actions"])
    assert actual == expected
