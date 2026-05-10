from __future__ import annotations

from app.cli.commands.tests import _build_synthetic_argv


def test_build_synthetic_argv_with_explicit_report_and_observations_dir() -> None:
    argv = _build_synthetic_argv(
        scenario="001-replication-lag",
        output_json=False,
        mock_grafana=True,
        report=True,
        observations_dir="/tmp/obs",
    )
    assert argv == [
        "--scenario",
        "001-replication-lag",
        "--mock-grafana",
        "--report",
        "--observations-dir",
        "/tmp/obs",
    ]


def test_build_synthetic_argv_with_json_and_no_report() -> None:
    argv = _build_synthetic_argv(
        scenario="",
        output_json=True,
        mock_grafana=False,
        report=False,
        observations_dir="",
    )
    assert argv == ["--json", "--no-report"]
