from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import has_credentials_for_active_llm_provider
from app.core.orchestration.entrypoints import run_investigation
from tests.synthetic.hermes_rca.scenario_loader import (
    SUITE_DIR,
    HermesScenarioFixture,
    load_all_scenarios,
    load_scenario,
)
from tests.synthetic.llm_provider_preflight import (
    UnsupportedSyntheticLLMProviderError,
    validate_synthetic_llm_provider,
)
from tests.synthetic.mock_hermes_backend.backend import FixtureHermesBackend

HISTORY_DIR = SUITE_DIR / "benchmark_history"


@dataclass(frozen=True)
class ScenarioScore:
    scenario_id: str
    passed: bool
    expected_category: str
    actual_category: str
    missing_keywords: list[str]
    forbidden_keywords_present: list[str]
    failure_reason: str = ""


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def _scored_output_text(final_state: dict[str, Any]) -> str:
    return " ".join(
        [
            str(final_state.get("root_cause") or ""),
            str(final_state.get("report") or ""),
            str(final_state.get("problem_md") or ""),
            " ".join(claim.get("claim", "") for claim in final_state.get("validated_claims", [])),
        ]
    )


def score_result(fixture: HermesScenarioFixture, final_state: dict[str, Any]) -> ScenarioScore:
    expected_category = fixture.answer_key.root_cause_category
    actual_category = str(final_state.get("root_cause_category") or "unknown").strip().lower()
    output = _normalized(_scored_output_text(final_state))

    missing_keywords = [
        kw for kw in fixture.answer_key.required_keywords if _normalized(kw) not in output
    ]
    forbidden_keywords_present = [
        kw for kw in fixture.answer_key.forbidden_keywords if _normalized(kw) in output
    ]

    forbidden = {item.strip().lower() for item in fixture.answer_key.forbidden_categories}
    category_forbidden = actual_category in forbidden

    if category_forbidden:
        return ScenarioScore(
            scenario_id=fixture.scenario_id,
            passed=False,
            expected_category=expected_category,
            actual_category=actual_category,
            missing_keywords=missing_keywords,
            forbidden_keywords_present=forbidden_keywords_present,
            failure_reason=f"forbidden category emitted: {actual_category}",
        )

    if actual_category != expected_category:
        return ScenarioScore(
            scenario_id=fixture.scenario_id,
            passed=False,
            expected_category=expected_category,
            actual_category=actual_category,
            missing_keywords=missing_keywords,
            forbidden_keywords_present=forbidden_keywords_present,
            failure_reason=f"wrong category: expected {expected_category}, got {actual_category}",
        )

    if forbidden_keywords_present:
        return ScenarioScore(
            scenario_id=fixture.scenario_id,
            passed=False,
            expected_category=expected_category,
            actual_category=actual_category,
            missing_keywords=missing_keywords,
            forbidden_keywords_present=forbidden_keywords_present,
            failure_reason=f"forbidden keywords present: {forbidden_keywords_present}",
        )

    if missing_keywords:
        return ScenarioScore(
            scenario_id=fixture.scenario_id,
            passed=False,
            expected_category=expected_category,
            actual_category=actual_category,
            missing_keywords=missing_keywords,
            forbidden_keywords_present=forbidden_keywords_present,
            failure_reason=f"missing required keywords: {missing_keywords}",
        )

    return ScenarioScore(
        scenario_id=fixture.scenario_id,
        passed=True,
        expected_category=expected_category,
        actual_category=actual_category,
        missing_keywords=[],
        forbidden_keywords_present=[],
    )


def _build_resolved_integrations(fixture: HermesScenarioFixture) -> dict[str, Any]:
    backend = FixtureHermesBackend(fixture)
    session_id = fixture.session_id()

    return {
        "hermes": {
            "connection_verified": True,
            "session_id": session_id,
            "_backend": backend,
        }
    }


def run_scenario(fixture: HermesScenarioFixture) -> tuple[dict[str, Any], ScenarioScore]:
    final_state = run_investigation(
        fixture.alert,
        resolved_integrations=_build_resolved_integrations(fixture),
    )
    final_state_dict = dict(final_state)
    return final_state_dict, score_result(fixture, final_state_dict)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run synthetic Hermes RCA suite.")
    parser.add_argument("--scenario", default="", help="Run one scenario directory name.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output")
    parser.add_argument(
        "--offline-only",
        action="store_true",
        help=(
            "Only validate scenario fixtures and answer keys (no LLM call). "
            "Useful for deterministic local/CI checks without provider keys."
        ),
    )
    parser.add_argument(
        "--write-history",
        action="store_true",
        help="Write a timestamped Hermes benchmark history snapshot.",
    )
    return parser.parse_args(argv)


def _offline_result(fixture: HermesScenarioFixture) -> dict[str, Any]:
    required_sources = set(fixture.answer_key.required_evidence_sources)
    available = set(fixture.metadata.available_evidence)
    missing_sources = sorted(required_sources - available)
    passed = not missing_sources
    return {
        "scenario_id": fixture.scenario_id,
        "status": "pass" if passed else "fail",
        "mode": "offline",
        "error": "" if passed else f"missing required evidence sources: {missing_sources}",
    }


def _write_history_snapshot(results: list[dict[str, Any]]) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    failed = sum(1 for item in results if item["status"] != "pass")
    passed = len(results) - failed
    pass_rate = passed / len(results) if results else 0.0

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = HISTORY_DIR / f"{timestamp}.json"

    payload = {
        "timestamp": timestamp,
        "suite": "hermes_rca",
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": pass_rate,
        "results": results,
    }

    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.scenario:
        scenarios = [load_scenario(SUITE_DIR / args.scenario)]
    else:
        scenarios = load_all_scenarios(SUITE_DIR)

    if not scenarios:
        print("No Hermes RCA scenarios found.", file=sys.stderr)
        return 1

    results: list[dict[str, Any]] = []

    if args.offline_only:
        for fixture in scenarios:
            results.append(_offline_result(fixture))
    else:
        try:
            validate_synthetic_llm_provider(
                suite_name="Hermes RCA",
                offline_hint="--offline-only",
            )
        except UnsupportedSyntheticLLMProviderError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        if not has_credentials_for_active_llm_provider():
            print(
                "Skipping LLM-backed Hermes RCA run: no credentials for active provider. "
                "Use --offline-only for deterministic checks.",
                file=sys.stderr,
            )
            return 0

        for fixture in scenarios:
            final_state, score = run_scenario(fixture)
            results.append(
                {
                    "scenario_id": fixture.scenario_id,
                    "status": "pass" if score.passed else "fail",
                    "mode": "llm",
                    "expected_category": score.expected_category,
                    "actual_category": score.actual_category,
                    "missing_keywords": score.missing_keywords,
                    "forbidden_keywords_present": score.forbidden_keywords_present,
                    "failure_reason": score.failure_reason,
                    "validity_score": final_state.get("validity_score"),
                }
            )

    failed = sum(1 for item in results if item["status"] != "pass")
    passed = len(results) - failed

    history_path = _write_history_snapshot(results) if args.write_history else None

    if args.json:
        print(
            json.dumps(
                {
                    "results": results,
                    "passed": passed,
                    "failed": failed,
                    "history_path": str(history_path) if history_path is not None else None,
                },
                indent=2,
            )
        )
    else:
        for item in results:
            print(f"[{item['status'].upper()}] {item['scenario_id']} ({item['mode']})")
            if item.get("failure_reason"):
                print(f"  reason: {item['failure_reason']}")
            if item.get("error"):
                print(f"  error: {item['error']}")
        print(f"\nResults: {passed}/{len(results)} passed")

        if history_path is not None:
            print(f"History snapshot: {history_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
