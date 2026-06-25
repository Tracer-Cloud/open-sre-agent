"""Live LLM contracts for the structured action planner."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TypedDict

import pytest
import yaml
from pydantic import ValidationError

from config.config import (
    DEFAULT_LLM_RESOLUTION_FALLBACK_PROVIDERS,
    get_configured_llm_provider,
    get_llm_provider_api_key_env,
    resolve_llm_settings_verbose,
)
from cli.interactive_shell.routing.handle_message_with_agent.command_dispatch import (
    deterministic_command_text,
)
from cli.interactive_shell.routing.handle_message_with_agent.errors import PlannerLLMError
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm,
)
from cli.interactive_shell.routing.router import route_input
from cli.interactive_shell.runtime.session import ReplSession

PROMPT_TURN_CONTRACTS_DATASET = Path(__file__).resolve().parents[1] / "prompt_turn_contracts.yml"

pytestmark = [pytest.mark.integration, pytest.mark.live_llm]

# The planner is a single live LLM sample per case, so stochastic output (most
# notably dropping/reordering one clause of a compound request) can flake an
# otherwise-correct mapping. Resample a bounded number of times: a genuinely
# wrong mapping keeps failing across every attempt, so this only absorbs
# nondeterminism and never bypasses the live planner decision.
_LIVE_PLAN_MAX_ATTEMPTS = 3


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
    # Capture the explicit pin (if any) BEFORE resolution so we can tell apart
    # "intentionally testing the default-resolution chain" from "CI pinned a
    # provider but its key is missing/broken".
    explicit_pin = os.environ.get("LLM_PROVIDER", "").strip().lower()

    try:
        resolution = resolve_llm_settings_verbose()
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

    # Anti-masking guard: when a provider is explicitly pinned (e.g. CI sets
    # LLM_PROVIDER=openai) but resolution falls back to a different provider, the
    # pinned provider's credentials are missing/broken. Silently validating the
    # contract on the fallback provider — then skipping on *its* billing error —
    # is exactly how the OPENAI_API_KEY leak hid 24 unrun cases. Fail loudly with
    # an actionable message instead.
    if explicit_pin and resolution.fell_back:
        pytest.fail(
            f"LLM_PROVIDER={explicit_pin!r} is pinned but provider resolution fell back to "
            f"{resolution.resolved_provider!r}. The pinned provider is unusable "
            f"({resolution.missing_key_env or 'credentials missing'}); live planner contracts "
            "would silently validate the wrong provider. Fix the pinned provider's credentials "
            "or unset LLM_PROVIDER to opt into default resolution."
        )

    from services.llm_client import reset_llm_singletons

    monkeypatch.setenv("LLM_PROVIDER", resolution.resolved_provider)
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
    actions, _has_unhandled = llm_plan
    if actions:
        return [_compact_action(action) for action in actions]

    # No executable actions: v0.1 has no fail-closed denial, so an empty plan is a
    # handoff. The case must therefore expect only assistant_handoff actions.
    assert case["expected_actions"] == [
        action for action in case["expected_actions"] if action["kind"] == "assistant_handoff"
    ]
    return case["expected_actions"]


# Provider-availability outages (billing, quota, rate limits, overload) are
# infrastructure conditions, not contract regressions: live planner cases skip
# rather than fail when the configured/fallback provider cannot serve a request.
_TRANSIENT_PROVIDER_TOKENS = (
    "usage limit",
    "rate limit",
    "quota",
    "billing",
    "credit balance",
    "temporarily unavailable",
    "service unavailable",
    "overloaded",
)


def _is_transient_provider_text(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in _TRANSIENT_PROVIDER_TOKENS)


def _is_transient_llm_provider_failure(records: list[logging.LogRecord]) -> bool:
    text = "\n".join(
        record.getMessage()
        for record in records
        if record.name.endswith("orchestration.llm_action_planner.llm_client")
    )
    return _is_transient_provider_text(text)


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
    expected = _normalize_for_assertion(case["expected_actions"])

    last_actual: list[ExpectedAction] | None = None
    for _attempt in range(_LIVE_PLAN_MAX_ATTEMPTS):
        caplog.clear()
        try:
            actual = _normalize_for_assertion(_actions_for_case(case))
        except PlannerLLMError as exc:
            # The planner raises on provider errors (billing/quota/overload); a
            # provider outage is an infra condition, not a contract regression.
            if _is_transient_provider_text(str(exc)):
                pytest.skip(f"Skipping live LLM planner case; provider unavailable: {exc}")
            raise
        if not actual and _is_transient_llm_provider_failure(caplog.records):
            pytest.skip("Skipping live LLM planner case due to transient provider/billing limits.")
        if not actual:
            pytest.fail("Live LLM action planner did not return a parseable plan.")
        last_actual = actual
        if actual == expected:
            return
        # Mismatch: resample the stochastic planner before failing. Deterministic
        # command dispatch returns the same result every attempt, so this only
        # gives genuinely nondeterministic LLM plans another draw.
    assert last_actual == expected
