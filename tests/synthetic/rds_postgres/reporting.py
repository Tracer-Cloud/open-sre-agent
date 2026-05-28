"""Rendering helpers for the synthetic RDS benchmark suite.

This module contains cross-axis report printers that are separate from the
per-scenario Rich observation report (which lives in observations.py).
"""

from __future__ import annotations

from tests.synthetic.rds_postgres.scenario_loader import ScenarioFixture
from tests.synthetic.rds_postgres.scoring import ScenarioScore


def print_gap_report(
    axis1_results: list[ScenarioScore],
    axis2_results: list[ScenarioScore],
    all_fixtures: list[ScenarioFixture],
) -> None:
    """Print Axis 1 vs Axis 2 pass-rate gap, overall and per difficulty level."""
    difficulty_map = {f.scenario_id: f.metadata.scenario_difficulty for f in all_fixtures}

    def _pass_rate(results: list[ScenarioScore]) -> float:
        return sum(1 for r in results if r.passed) / len(results) * 100 if results else 0.0

    ax1_pct = _pass_rate(axis1_results)
    ax2_pct = _pass_rate(axis2_results)
    gap = ax1_pct - ax2_pct

    print("\n=== Axis 1 vs Axis 2 Gap Report ===")
    print(
        f"  Axis 1 (all scenarios, full data):  {ax1_pct:.0f}%"
        f"  ({sum(r.passed for r in axis1_results)}/{len(axis1_results)})"
    )
    print(
        f"  Axis 2 (adversarial, selective):    {ax2_pct:.0f}%"
        f"  ({sum(r.passed for r in axis2_results)}/{len(axis2_results)})"
    )
    print(f"  Gap:                                {gap:+.0f}pp")

    print("\n  Per difficulty level:")
    for level in sorted(
        {difficulty_map.get(r.scenario_id, 0) for r in axis1_results + axis2_results}
    ):
        ax1_level = [r for r in axis1_results if difficulty_map.get(r.scenario_id, 0) == level]
        ax2_level = [r for r in axis2_results if difficulty_map.get(r.scenario_id, 0) == level]
        ax1_pct_l = _pass_rate(ax1_level)
        ax2_pct_l = _pass_rate(ax2_level)
        gap_l = ax1_pct_l - ax2_pct_l
        print(
            f"    Difficulty {level}: Axis1={ax1_pct_l:.0f}% ({len(ax1_level)} scenarios)  "
            f"Axis2={ax2_pct_l:.0f}% ({len(ax2_level)} scenarios)  gap={gap_l:+.0f}pp"
        )
