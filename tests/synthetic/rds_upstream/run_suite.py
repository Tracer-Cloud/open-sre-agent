"""Thin orchestration entrypoint for the rds_upstream synthetic suite (#1437).

Usage
-----
    uv run python -m tests.synthetic.rds_upstream.run_suite --offline-only
    uv run python -m tests.synthetic.rds_upstream.run_suite
    uv run python -m tests.synthetic.rds_upstream.run_suite --scenario 001-request-burst-ec2-app-tier
    uv run python -m tests.synthetic.rds_upstream.run_suite --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from config.config import has_credentials_for_active_llm_provider
from tests.synthetic.mock_aws_backend import FixtureAWSBackend
from tests.synthetic.mock_grafana_backend.backend import FixtureGrafanaBackend
from tests.synthetic.rds_postgres.scenario_loader import ScenarioFixture
from tests.synthetic.rds_postgres.scoring import ScenarioScore, score_result
from tests.synthetic.rds_upstream.scenario_loader import (
    SUITE_DIR,
    load_all_scenarios,
    load_scenario,
)
from tests.synthetic.schemas import VALID_EVIDENCE_SOURCES
from tools.investigation.capability import run_investigation


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the synthetic RDS → upstream (EC2/ALB) RCA suite.",
    )
    parser.add_argument(
        "--scenario",
        default="",
        help="Run a single scenario directory name, e.g. 001-request-burst-ec2-app-tier.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON results.",
    )
    parser.add_argument(
        "--offline-only",
        action="store_true",
        help=(
            "Validate fixtures and answer keys only (no LLM call). "
            "Useful for deterministic local/CI checks without provider keys."
        ),
    )
    return parser.parse_args(argv)


def _build_resolved_integrations(fixture: ScenarioFixture) -> dict[str, Any]:
    return {
        "grafana": {
            "endpoint": "",
            "api_key": "",
            "_backend": FixtureGrafanaBackend(fixture),
        },
        "aws": {
            "region": fixture.metadata.region,
            "ec2_backend": FixtureAWSBackend(fixture),
        },
    }


def _offline_result(fixture: ScenarioFixture) -> dict[str, Any]:
    unknown_evidence = set(fixture.metadata.available_evidence) - VALID_EVIDENCE_SOURCES
    missing_sources = sorted(
        set(fixture.answer_key.required_evidence_sources) - set(fixture.metadata.available_evidence)
    )
    evidence_dict = fixture.evidence.as_dict()
    evidence_keys_ok = set(evidence_dict.keys()) == set(fixture.metadata.available_evidence)

    if unknown_evidence:
        error = f"unknown evidence sources: {sorted(unknown_evidence)}"
    elif missing_sources:
        error = f"missing required evidence sources: {missing_sources}"
    elif not evidence_keys_ok:
        error = (
            "fixture evidence keys do not match metadata.available_evidence: "
            f"got {sorted(evidence_dict.keys())}, "
            f"expected {sorted(fixture.metadata.available_evidence)}"
        )
    else:
        error = ""

    return {
        "scenario_id": fixture.scenario_id,
        "status": "pass" if not error else "fail",
        "mode": "offline",
        "error": error,
    }


def run_scenario(fixture: ScenarioFixture) -> tuple[dict[str, Any], ScenarioScore]:
    final_state = run_investigation(
        fixture.alert,
        resolved_integrations=_build_resolved_integrations(fixture),
    )
    state_dict = dict(final_state)
    return state_dict, score_result(fixture, state_dict)


def _select_fixtures(scenario: str) -> list[ScenarioFixture]:
    if scenario:
        return [load_scenario(SUITE_DIR / scenario)]
    return load_all_scenarios(SUITE_DIR)


def run_suite(argv: list[str] | None = None) -> list[ScenarioScore | dict[str, Any]]:
    args = parse_args(argv)
    fixtures = _select_fixtures(str(args.scenario or "").strip())
    if not fixtures:
        raise SystemExit("No rds_upstream scenarios found.")

    if args.offline_only:
        results: list[ScenarioScore | dict[str, Any]] = [
            _offline_result(fixture) for fixture in fixtures
        ]
        _print_results(results, output_json=bool(args.json))
        return results

    if not has_credentials_for_active_llm_provider():
        print(
            "Skipping LLM-backed rds_upstream run: no credentials for active provider. "
            "Use --offline-only for deterministic checks.",
            file=sys.stderr,
        )
        return []

    scores: list[ScenarioScore | dict[str, Any]] = []
    for fixture in fixtures:
        final_state, score = run_scenario(fixture)
        scores.append(
            {
                "scenario_id": fixture.scenario_id,
                "status": "pass" if score.passed else "fail",
                "mode": "llm",
                "expected_category": score.expected_category,
                "actual_category": score.actual_category,
                "missing_keywords": score.missing_keywords,
                "failure_reason": score.failure_reason,
                "validity_score": final_state.get("validity_score"),
                "score": asdict(score),
            }
        )

    _print_results(scores, output_json=bool(args.json))
    return scores


def _print_results(
    results: list[ScenarioScore | dict[str, Any]],
    *,
    output_json: bool,
) -> None:
    if output_json:
        print(json.dumps(results, indent=2))
        return

    for item in results:
        if isinstance(item, ScenarioScore):
            status = "PASS" if item.passed else "FAIL"
            detail = item.failure_reason or f"category={item.actual_category}"
            print(f"{status} {item.scenario_id} {detail}")
            continue

        status = item["status"].upper()
        mode = item.get("mode", "unknown")
        print(f"[{status}] {item['scenario_id']} ({mode})")
        if item.get("failure_reason"):
            print(f"  reason: {item['failure_reason']}")
        if item.get("error"):
            print(f"  error: {item['error']}")

    passed_count = sum(
        1
        for item in results
        if (item.passed if isinstance(item, ScenarioScore) else item.get("status") == "pass")
    )
    print(f"\nResults: {passed_count}/{len(results)} passed")


def main(argv: list[str] | None = None) -> int:
    results = run_suite(argv)
    if not results:
        return 0

    passed = all(
        item.passed if isinstance(item, ScenarioScore) else item.get("status") == "pass"
        for item in results
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
