"""Axis-memory runner: run pair scenarios and emit memory-mode annotations.

Two modes:

  baseline (default): run each `sibling` scenario with no memory primed and
    record the pass/fail outcome. This is the only mode that ships today;
    Contextual Memory (issue #1234) is not wired up yet.

  memory: run each `sibling` with memory primed from its `base`, then call
    `annotate_pair(...)` with both baseline and memory ScenarioScores. Wired
    up later once #1234's memory layer lands. The runner is structured so
    enabling this mode is a single-flag change rather than a rewrite.

Usage:

    python -m tests.synthetic.rds_postgres.axis_memory.run_axis_memory
    python -m tests.synthetic.rds_postgres.axis_memory.run_axis_memory --pair connection-pressure-real-vs-noisy
    python -m tests.synthetic.rds_postgres.axis_memory.run_axis_memory --json
    python -m tests.synthetic.rds_postgres.axis_memory.run_axis_memory --memory   # placeholder until #1234

The output prints one line per pair plus a tally summary at the end. With
`--json`, the full annotation list is emitted as JSON for downstream
consumption.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from tests.synthetic.rds_postgres.axis_memory.scoring import (
    MemoryMode,
    PairAnnotation,
    annotate_pair,
    summarize_annotations,
)
from tests.synthetic.rds_postgres.run_suite import run_scenario
from tests.synthetic.rds_postgres.scenario_loader import (
    SUITE_DIR,
    load_all_scenarios,
)

PAIRS_FILE = Path(__file__).parent / "pairs.yml"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run axis-memory pairs and emit memory-mode annotations."
    )
    parser.add_argument(
        "--pair",
        default="",
        help="Run only the pair with this id (see pairs.yml).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )
    parser.add_argument(
        "--memory",
        action="store_true",
        help=(
            "Run sibling with memory primed from base (requires Contextual Memory "
            "from issue #1234). Currently a placeholder; raises NotImplementedError."
        ),
    )
    parser.add_argument(
        "--mock-grafana",
        action="store_true",
        help="Serve fixture data via FixtureGrafanaBackend instead of real Grafana calls.",
    )
    return parser.parse_args(argv)


def load_pairs(only_id: str = "") -> list[dict[str, Any]]:
    """Read pairs.yml; filter to active pairs (and optionally a single id)."""
    raw = yaml.safe_load(PAIRS_FILE.read_text())
    pairs: list[dict[str, Any]] = list(raw.get("pairs", []) or [])
    pairs = [p for p in pairs if p.get("active", True)]
    if only_id:
        pairs = [p for p in pairs if p.get("id") == only_id]
        if not pairs:
            raise SystemExit(f"No active pair with id: {only_id!r}")
    return pairs


def _run_baseline(scenario_id: str, fixtures_by_id: dict[str, Any], use_mock_grafana: bool) -> Any:
    """Run a single scenario without memory and return its ScenarioScore."""
    fixture = fixtures_by_id.get(scenario_id)
    if fixture is None:
        raise SystemExit(f"Scenario fixture not found: {scenario_id!r}")
    _state, score = run_scenario(fixture, use_mock_grafana=use_mock_grafana)
    return score


def _run_with_memory(
    base_scenario_id: str,
    sibling_scenario_id: str,
    fixtures_by_id: dict[str, Any],
    use_mock_grafana: bool,
) -> Any:
    """Run sibling with memory primed from base.

    Placeholder until issue #1234 (Contextual Memory) lands. The full path is:

        1. Run base, capture investigation memory output.
        2. Inject that memory into the sibling investigation context.
        3. Run sibling and return its ScenarioScore.

    Step 2 needs the memory layer that #1234 will introduce. Until then this
    raises NotImplementedError so the runner fails loudly rather than
    silently degrading to baseline behaviour.
    """
    raise NotImplementedError(
        "Memory-primed mode requires Contextual Memory from issue #1234. "
        "Run without --memory for the baseline scaffold."
    )


def run(argv: list[str] | None = None) -> list[PairAnnotation]:
    args = parse_args(argv)
    pairs = load_pairs(only_id=args.pair)

    fixtures = load_all_scenarios(SUITE_DIR)
    fixtures_by_id = {fixture.scenario_id: fixture for fixture in fixtures}

    annotations: list[PairAnnotation] = []
    for pair in pairs:
        pair_id = pair["id"]
        base_id = pair["base"]
        sibling_id = pair["sibling"]

        baseline_score = _run_baseline(sibling_id, fixtures_by_id, args.mock_grafana)
        memory_score = None
        if args.memory:
            memory_score = _run_with_memory(
                base_id, sibling_id, fixtures_by_id, args.mock_grafana
            )

        annotations.append(
            annotate_pair(
                pair_id=pair_id,
                base_scenario_id=base_id,
                sibling_scenario_id=sibling_id,
                baseline_score=baseline_score,
                memory_score=memory_score,
            )
        )

    if args.json:
        # Shallow dict serialization; PairAnnotation is a frozen dataclass.
        payload = {
            "annotations": [_annotation_to_dict(a) for a in annotations],
            "summary": summarize_annotations(annotations),
        }
        print(json.dumps(payload, indent=2))
    else:
        _print_human(annotations)

    return annotations


def _annotation_to_dict(annotation: PairAnnotation) -> dict[str, Any]:
    payload = asdict(annotation)
    # Enums serialize as their string value when json.dumps default=str is used,
    # but be explicit for predictability.
    payload["memory_mode"] = annotation.memory_mode.value
    return payload


def _print_human(annotations: list[PairAnnotation]) -> None:
    for annotation in annotations:
        baseline_str = _passed_str(annotation.baseline_passed)
        memory_str = _passed_str(annotation.memory_passed)
        print(
            f"  pair={annotation.pair_id}  "
            f"sibling={annotation.sibling_scenario_id}  "
            f"baseline={baseline_str}  memory={memory_str}  "
            f"mode={annotation.memory_mode.value}"
        )
    tally = summarize_annotations(annotations)
    nonzero = {k: v for k, v in tally.items() if v > 0}
    print()
    print("  Summary:")
    for mode in MemoryMode:
        count = tally[mode.value]
        marker = " " if count == 0 else "*"
        print(f"    {marker} {mode.value:<16} {count}")
    # Also print a concise top-line if there's anything memory-attributable.
    if nonzero.get(MemoryMode.MEMORY_HURT.value):
        print()
        print(
            f"  WARNING: {nonzero[MemoryMode.MEMORY_HURT.value]} pair(s) "
            "show memory-attributable regression. Investigate before shipping #1234 Phase 1."
        )


def _passed_str(passed: bool | None) -> str:
    if passed is True:
        return "PASS"
    if passed is False:
        return "FAIL"
    return "----"


if __name__ == "__main__":
    sys.exit(0 if run() is not None else 1)
