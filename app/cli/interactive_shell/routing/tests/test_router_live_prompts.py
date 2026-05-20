"""Live LLM routing contracts for the top-level router."""

from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter
from typing import TypedDict

import pytest
import yaml
from pydantic import ValidationError

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    plan_actions_with_llm,
)
from app.cli.interactive_shell.routing.llm_intent_classifier import clear_classify_cache
from app.cli.interactive_shell.routing.router import RouteKind, route_input
from app.cli.interactive_shell.runtime.session import ReplSession
from app.config import LLMSettings, get_configured_llm_provider, get_llm_provider_api_key_env

TESTS_DIR = Path(__file__).resolve().parent
MAX_UNCERTAIN_RETRIES = 3


class ExpectedAction(TypedDict):
    kind: str
    content: str


class RouterLivePromptCase(TypedDict):
    id: str
    input: str
    expected_kind: str
    expected_actions: list[ExpectedAction]
    with_prior_state: bool


pytestmark = [pytest.mark.integration, pytest.mark.live_llm]


def _load_prompt_cases(filename: str) -> list[RouterLivePromptCase]:
    payload = yaml.safe_load((TESTS_DIR / filename).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        msg = f"Fixture {filename} must contain a top-level YAML list"
        raise ValueError(msg)

    cases: list[RouterLivePromptCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(payload):
        if not isinstance(raw_case, dict):
            msg = f"Fixture {filename} case {index} must be a mapping"
            raise ValueError(msg)
        case_id = str(raw_case.get("id", "")).strip()
        if not case_id:
            msg = f"Fixture {filename} case {index} has empty 'id'"
            raise ValueError(msg)
        if case_id in seen_ids:
            msg = f"Fixture {filename} contains duplicate id {case_id!r}"
            raise ValueError(msg)
        seen_ids.add(case_id)

        expected_kind = str(raw_case.get("expected_kind", "")).strip()
        if expected_kind not in {kind.value for kind in RouteKind}:
            msg = f"Fixture {filename} case {case_id!r} has invalid expected_kind {expected_kind!r}"
            raise ValueError(msg)
        raw_actions = raw_case.get("expected_actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            msg = f"Fixture {filename} case {case_id!r} must define non-empty expected_actions"
            raise ValueError(msg)
        expected_actions: list[ExpectedAction] = []
        for action_idx, raw_action in enumerate(raw_actions):
            if not isinstance(raw_action, dict):
                msg = (
                    f"Fixture {filename} case {case_id!r} expected_actions[{action_idx}] "
                    "must be a mapping"
                )
                raise ValueError(msg)
            kind = str(raw_action.get("kind", "")).strip()
            content = str(raw_action.get("content", "")).strip()
            if not kind or not content:
                msg = (
                    f"Fixture {filename} case {case_id!r} expected_actions[{action_idx}] "
                    "must define non-empty kind and content"
                )
                raise ValueError(msg)
            expected_actions.append({"kind": kind, "content": content})

        case: RouterLivePromptCase = {
            "id": case_id,
            "input": str(raw_case.get("input", "")),
            "expected_kind": expected_kind,
            "expected_actions": expected_actions,
            "with_prior_state": bool(raw_case.get("with_prior_state", False)),
        }
        if not case["input"].strip():
            msg = f"Fixture {filename} case {case_id!r} has empty 'input'"
            raise ValueError(msg)
        cases.append(case)
    return cases


def _read_shard_config() -> tuple[int, int]:
    total = int(os.getenv("ROUTING_SHARD_TOTAL", "1"))
    index = int(os.getenv("ROUTING_SHARD_INDEX", "0"))
    if total < 1:
        msg = "ROUTING_SHARD_TOTAL must be >= 1"
        raise ValueError(msg)
    if index < 0 or index >= total:
        msg = "ROUTING_SHARD_INDEX must satisfy 0 <= index < ROUTING_SHARD_TOTAL"
        raise ValueError(msg)
    return total, index


def _filter_cases_for_shard(cases: list[RouterLivePromptCase]) -> list[RouterLivePromptCase]:
    total, index = _read_shard_config()
    return [case for offset, case in enumerate(cases) if offset % total == index]


def _fresh_session(*, with_prior_state: bool) -> ReplSession:
    session = ReplSession()
    if with_prior_state:
        session.last_state = {"root_cause": "disk full on orders-api"}
    return session


def _is_uncertain_fallback(actual_kind: str, fallback_reason: str | None) -> bool:
    return fallback_reason is not None and actual_kind == RouteKind.CLI_AGENT.value


_ALL_CASES = _load_prompt_cases("router_live_prompts.yml")
_SHARDED_CASES = _filter_cases_for_shard(_ALL_CASES)
_NITRO_CONNECT_PROMPT = (
    "I want to connect to OpenSRE that I have running on a remote EC2 Nitro instance, "
    "and then I want to send it an investigation. Can you please connect the instance "
    'and send it "hello world"'
)


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
        pytest.fail(f"Live LLM routing tests require default LLM configuration:{hint}. {msg}")


@pytest.fixture(autouse=True)
def _clear_classify_cache() -> None:
    clear_classify_cache()


def test_shard_selection_is_non_empty() -> None:
    if _SHARDED_CASES:
        return
    total, index = _read_shard_config()
    pytest.skip(f"No routing cases selected for shard {index}/{total}.")


def _route_kind_with_uncertain_retries(
    *,
    case_id: str,
    text: str,
    session: ReplSession,
    expected_kind: str,
) -> str:
    started_at = perf_counter()
    decision = route_input(text, session)
    latency_ms = int((perf_counter() - started_at) * 1000)
    actual_kind = decision.route_kind.value
    print(
        f"routing_live_case id={case_id} expected={expected_kind} "
        f"actual={actual_kind} latency_ms={latency_ms}"
    )

    attempts = 1
    while _is_uncertain_fallback(actual_kind, decision.fallback_reason):
        if attempts >= MAX_UNCERTAIN_RETRIES:
            break
        clear_classify_cache()
        started_at = perf_counter()
        decision = route_input(text, session)
        latency_ms = int((perf_counter() - started_at) * 1000)
        attempts += 1
        actual_kind = decision.route_kind.value
        print(
            f"routing_live_case id={case_id} expected={expected_kind} "
            f"actual={actual_kind} latency_ms={latency_ms}"
        )

    return actual_kind


def _compact_action(action: PlannedAction) -> ExpectedAction:
    return {"kind": action.kind, "content": action.content}


def _expected_assistant_handoff(case: RouterLivePromptCase) -> ExpectedAction | None:
    if len(case["expected_actions"]) != 1:
        return None
    action = case["expected_actions"][0]
    return action if action["kind"] == "assistant_handoff" else None


def _live_actions_for_case(
    case: RouterLivePromptCase,
    session: ReplSession,
) -> list[ExpectedAction]:
    decision = route_input(case["input"], session)
    if decision.route_kind == RouteKind.SLASH:
        return [
            {
                "kind": "slash",
                "content": decision.command_text or case["input"].strip(),
            }
        ]

    llm_plan = plan_actions_with_llm(case["input"])
    assert llm_plan is not None, "Live LLM action planner did not return a parseable plan."
    actions, has_unhandled = llm_plan
    if actions:
        return [_compact_action(action) for action in actions]

    handoff = _expected_assistant_handoff(case)
    assert handoff is not None, "No executable actions planned, but fixture expects actions."
    assert has_unhandled is True
    return [handoff]


@pytest.mark.parametrize("case", _SHARDED_CASES, ids=lambda case: case["id"])
def test_router_live_prompts(case: RouterLivePromptCase) -> None:
    session = _fresh_session(with_prior_state=case["with_prior_state"])
    expected_kind = case["expected_kind"]
    actual_kind = _route_kind_with_uncertain_retries(
        case_id=case["id"],
        text=case["input"],
        session=session,
        expected_kind=expected_kind,
    )

    assert actual_kind == expected_kind
    assert _live_actions_for_case(case, session) == case["expected_actions"]


def test_router_live_prompt_nitro_connect_routes_to_cli_agent() -> None:
    expected_kind = RouteKind.CLI_AGENT.value
    session = _fresh_session(with_prior_state=False)
    actual_kind = _route_kind_with_uncertain_retries(
        case_id="nitro_connect_live",
        text=_NITRO_CONNECT_PROMPT,
        session=session,
        expected_kind=expected_kind,
    )

    assert actual_kind == expected_kind
