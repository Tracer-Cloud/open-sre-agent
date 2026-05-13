"""Runner for synthetic OpenClaw investigation scenarios.

Each scenario provides:
  - alert.json          Sparse alert (no embedded root cause)
  - openclaw_conversations.json  Fixture conversations served by FixtureOpenClawBackend
  - scenario.json       Expected keywords and description

The runner patches app.integrations.openclaw.call_openclaw_tool and
list_openclaw_tools so the investigation pipeline exercises the full tool
call path without a live OpenClaw process.

Usage
-----
    python -m tests.synthetic.openclaw.run_suite
    python -m tests.synthetic.openclaw.run_suite --scenario db_connection_pool_exhausted
    python -m tests.synthetic.openclaw.run_suite --json
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from typing import Any

from tests.synthetic.mock_openclaw_backend.backend import FixtureOpenClawBackend
from tests.synthetic.openclaw.scenario_loader import (
    OpenClawScenario,
    load_all_scenarios,
    load_scenario,
)


def _openclaw_resolved_integrations() -> dict[str, Any]:
    """Return a resolved_integrations dict that enables OpenClaw tools."""
    return {
        "openclaw": {
            "connection_verified": True,
            "mode": "stdio",
            "command": "openclaw",
            "args": ["mcp", "serve"],
            "url": "",
            "auth_token": "",
        }
    }


def _root_cause_text(state: dict[str, Any]) -> str:
    root_cause = state.get("root_cause") or ""
    diagnosis = state.get("diagnosis") or ""
    findings = state.get("findings") or ""
    return " ".join(filter(None, [str(root_cause), str(diagnosis), str(findings)])).lower()


def _check_keywords(root_cause_text: str, keywords: list[str]) -> tuple[bool, list[str]]:
    matched = [kw for kw in keywords if kw.lower() in root_cause_text]
    return bool(matched), matched


def run_scenario(scenario: OpenClawScenario) -> dict[str, Any]:
    """Run one scenario and return a result dict."""
    from app.pipeline.runners import run_investigation

    backend = FixtureOpenClawBackend(scenario)
    resolved = _openclaw_resolved_integrations()

    start = time.monotonic()
    try:
        with backend.patch():
            state = run_investigation(
                alert_name=scenario.alert_name,
                pipeline_name=scenario.pipeline_name,
                severity=scenario.severity,
                raw_alert=scenario.alert,
                resolved_integrations=resolved,
            )
        elapsed = time.monotonic() - start
        root_cause_text = _root_cause_text(dict(state))
        passed, matched_keywords = _check_keywords(
            root_cause_text, scenario.expected_root_cause_keywords
        )
        return {
            "scenario_id": scenario.scenario_id,
            "status": "pass" if passed else "fail",
            "elapsed_seconds": round(elapsed, 1),
            "root_cause": str(state.get("root_cause") or ""),
            "matched_keywords": matched_keywords,
            "expected_keywords": scenario.expected_root_cause_keywords,
            "error": None,
        }
    except Exception as exc:
        elapsed = time.monotonic() - start
        return {
            "scenario_id": scenario.scenario_id,
            "status": "error",
            "elapsed_seconds": round(elapsed, 1),
            "root_cause": "",
            "matched_keywords": [],
            "expected_keywords": scenario.expected_root_cause_keywords,
            "error": str(exc),
        }


def _print_result(result: dict[str, Any]) -> None:
    status_icon = {"pass": "✓", "fail": "✗", "error": "!"}.get(result["status"], "?")
    print(
        f"  [{status_icon}] {result['scenario_id']}  "
        f"({result['elapsed_seconds']}s)  {result['status'].upper()}"
    )
    if result["error"]:
        print(f"      error: {result['error']}")
    if result["status"] == "fail":
        print(f"      root_cause: {result['root_cause'][:200]}")
        print(f"      expected keywords: {result['expected_keywords']}")
        print(f"      matched: {result['matched_keywords']}")
    elif result["status"] == "pass":
        print(f"      matched keywords: {result['matched_keywords']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run synthetic OpenClaw investigation scenarios.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Each scenario provides a sparse alert and fixture OpenClaw conversations.
            The agent must query OpenClaw bridge tools to discover the root cause.
        """),
    )
    parser.add_argument("--scenario", default="", help="Run a single scenario by directory name.")
    parser.add_argument(
        "--json", dest="output_json", action="store_true", help="Print JSON results."
    )
    args, _ = parser.parse_known_args(argv)
    output_json: bool = bool(args.output_json)

    if args.scenario:
        try:
            scenarios = [load_scenario(args.scenario)]
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    else:
        scenarios = load_all_scenarios()

    if not scenarios:
        print("No scenarios found.", file=sys.stderr)
        return 1

    if not output_json:
        print(f"\nOpenClaw synthetic suite — {len(scenarios)} scenario(s)\n")

    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        if not output_json:
            print(f"  Running: {scenario.scenario_id}  [{scenario.description[:60]}]")
        result = run_scenario(scenario)
        results.append(result)
        if not output_json:
            _print_result(result)

    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] != "pass")

    if output_json:
        print(json.dumps({"results": results, "passed": passed, "failed": failed}, indent=2))
        return 0 if failed == 0 else 1

    print(f"\nResults: {passed}/{len(results)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
