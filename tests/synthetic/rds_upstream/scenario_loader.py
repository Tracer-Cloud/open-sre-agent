"""Scenario loader for the cross-system RDS → upstream synthetic suite (#1437)."""

from __future__ import annotations

from pathlib import Path

from tests.synthetic.rds_postgres.scenario_loader import (
    GoldenTrajectoryConfig,
    ScenarioAnswerKey,
    ScenarioFixture,
    ScenarioMetadata,
)
from tests.synthetic.rds_postgres.scenario_loader import (
    load_all_scenarios as _load_all_scenarios,
)
from tests.synthetic.rds_postgres.scenario_loader import (
    load_scenario as _load_scenario,
)

SUITE_DIR = Path(__file__).resolve().parent


def load_scenario(scenario_dir: Path) -> ScenarioFixture:
    return _load_scenario(scenario_dir)


def load_all_scenarios(root_dir: Path | None = None) -> list[ScenarioFixture]:
    return _load_all_scenarios(root_dir or SUITE_DIR)


__all__ = [
    "SUITE_DIR",
    "GoldenTrajectoryConfig",
    "ScenarioAnswerKey",
    "ScenarioFixture",
    "ScenarioMetadata",
    "load_all_scenarios",
    "load_scenario",
]
