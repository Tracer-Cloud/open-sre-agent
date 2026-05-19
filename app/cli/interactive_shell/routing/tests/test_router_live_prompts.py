"""Live LLM routing contracts for the top-level router."""

from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter
from typing import TypedDict

import pytest
import yaml

from app.cli.interactive_shell.orchestration.llm_intent_classifier import clear_classify_cache
from app.cli.interactive_shell.routing.router import RouteKind, route_input
from app.cli.interactive_shell.runtime.session import ReplSession

TESTS_DIR = Path(__file__).resolve().parent
MAX_UNCERTAIN_RETRIES = 3


class RouterLivePromptCase(TypedDict):
    id: str
    input: str
    expected_kind: str
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

        case: RouterLivePromptCase = {
            "id": case_id,
            "input": str(raw_case.get("input", "")),
            "expected_kind": expected_kind,
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


@pytest.fixture(autouse=True)
def _require_anthropic_api_key() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("Live LLM routing tests require ANTHROPIC_API_KEY.")


@pytest.fixture(autouse=True)
def _clear_classify_cache() -> None:
    clear_classify_cache()


def test_shard_selection_is_non_empty() -> None:
    if _SHARDED_CASES:
        return
    total, index = _read_shard_config()
    pytest.skip(f"No routing cases selected for shard {index}/{total}.")


@pytest.mark.parametrize("case", _SHARDED_CASES, ids=lambda case: case["id"])
def test_router_live_prompts(case: RouterLivePromptCase) -> None:
    session = _fresh_session(with_prior_state=case["with_prior_state"])
    expected_kind = case["expected_kind"]

    started_at = perf_counter()
    decision = route_input(case["input"], session)
    latency_ms = int((perf_counter() - started_at) * 1000)
    actual_kind = decision.route_kind.value
    print(
        f"routing_live_case id={case['id']} expected={expected_kind} "
        f"actual={actual_kind} latency_ms={latency_ms}"
    )

    attempts = 1
    while _is_uncertain_fallback(actual_kind, decision.fallback_reason):
        if attempts >= MAX_UNCERTAIN_RETRIES:
            break
        clear_classify_cache()
        started_at = perf_counter()
        decision = route_input(case["input"], session)
        latency_ms = int((perf_counter() - started_at) * 1000)
        attempts += 1
        actual_kind = decision.route_kind.value
        print(
            f"routing_live_case id={case['id']} expected={expected_kind} "
            f"actual={actual_kind} latency_ms={latency_ms}"
        )

    assert actual_kind == expected_kind
