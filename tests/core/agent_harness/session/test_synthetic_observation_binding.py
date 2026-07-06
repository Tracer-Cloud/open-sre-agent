"""Regression tests for post-synthetic-failure observation path binding."""

from __future__ import annotations

import unittest.mock
from pathlib import Path

from core.agent_harness.session.state import Session


def test_suggest_synthetic_failure_follow_up_binds_observation_path(tmp_path: Path) -> None:
    scenario_id = "001-replication-lag"
    latest = (
        tmp_path
        / "tests"
        / "synthetic"
        / "rds_postgres"
        / "_observations"
        / scenario_id
        / "latest.json"
    )
    latest.parent.mkdir(parents=True)
    latest.write_text('{"status": "failed"}', encoding="utf-8")

    session = Session()
    with unittest.mock.patch("config.constants.paths.REPO_ROOT", tmp_path):
        session.suggest_synthetic_failure_follow_up(
            label="opensre tests synthetic --scenario 001-replication-lag",
        )

    assert session.last_synthetic_observation_path == str(latest.resolve())


def test_suggest_synthetic_failure_follow_up_invalid_scenario_clears_path() -> None:
    session = Session()
    session.last_synthetic_observation_path = "/tmp/stale.json"

    session.suggest_synthetic_failure_follow_up(label="opensre tests synthetic --scenario ./evil")

    assert session.last_synthetic_observation_path is None


def test_suggest_synthetic_failure_follow_up_missing_observation_clears_path(
    tmp_path: Path,
) -> None:
    (tmp_path / "tests" / "synthetic" / "rds_postgres").mkdir(parents=True)

    session = Session()
    with (
        unittest.mock.patch("config.constants.paths.REPO_ROOT", tmp_path),
        unittest.mock.patch("core.agent_harness.session.state.time.sleep"),
    ):
        session.suggest_synthetic_failure_follow_up(
            label="opensre tests synthetic --scenario 001-replication-lag",
        )

    assert session.last_synthetic_observation_path is None
