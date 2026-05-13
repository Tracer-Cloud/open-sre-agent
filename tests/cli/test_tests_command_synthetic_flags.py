from __future__ import annotations

import sys
import types
import unittest.mock
from pathlib import Path

from click.testing import CliRunner

# ``app.cli.commands`` imports ``app.agents.probe`` via command registration.
# ``probe`` depends on optional ``psutil``; provide a tiny stub so this
# focused CLI argv-plumbing test remains hermetic in minimal environments.
if "psutil" not in sys.modules:
    psutil_stub = types.ModuleType("psutil")
    psutil_stub.Process = object
    psutil_stub.pid_exists = lambda _pid: False

    class _PsutilStubError(Exception):
        pass

    psutil_stub.NoSuchProcess = _PsutilStubError
    psutil_stub.ZombieProcess = _PsutilStubError
    psutil_stub.AccessDenied = _PsutilStubError
    sys.modules["psutil"] = psutil_stub

from app.cli.__main__ import cli
from app.cli.commands.tests import _build_openclaw_synthetic_argv, _build_synthetic_argv


def test_build_synthetic_argv_with_explicit_report_and_observations_dir() -> None:
    argv = _build_synthetic_argv(
        scenario="001-replication-lag",
        levels="1,2,3,4",
        parallel_levels=1,
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
        levels="1,2,3,4",
        parallel_levels=1,
        output_json=True,
        mock_grafana=False,
        report=False,
        observations_dir="",
    )
    assert argv == ["--json", "--no-report"]


def test_build_synthetic_argv_with_levels_and_parallel() -> None:
    argv = _build_synthetic_argv(
        scenario="",
        levels="2,3,4",
        parallel_levels=4,
        output_json=False,
        mock_grafana=True,
        report=None,
        observations_dir="",
    )
    assert argv == ["--levels", "2,3,4", "--parallel-levels", "4", "--mock-grafana"]


def test_build_openclaw_synthetic_argv() -> None:
    argv = _build_openclaw_synthetic_argv(
        scenario="gateway_process_terminated_missing_tls_key",
        output_json=True,
    )

    assert argv == ["--scenario", "gateway_process_terminated_missing_tls_key", "--json"]


def test_tests_synthetic_cli_forwards_flags_to_run_suite_main(tmp_path: Path) -> None:
    runner = CliRunner()
    observations_dir = tmp_path / "obs"
    scenarios_dir = tmp_path / "rds_postgres"
    (scenarios_dir / "001-replication-lag").mkdir(parents=True)

    seen_argv: list[str] = []

    def _fake_main(argv: list[str]) -> int:
        seen_argv[:] = argv
        return 3

    fake_run_suite = types.ModuleType("tests.synthetic.rds_postgres.run_suite")
    fake_run_suite.main = _fake_main

    with (
        unittest.mock.patch("app.cli.tests.discover.SYNTHETIC_SCENARIOS_DIR", scenarios_dir),
        unittest.mock.patch.dict(
            sys.modules,
            {"tests.synthetic.rds_postgres.run_suite": fake_run_suite},
        ),
    ):
        result = runner.invoke(
            cli,
            [
                "tests",
                "synthetic",
                "--scenario",
                "001-replication-lag",
                "--levels",
                "2,3,4",
                "--parallel-levels",
                "4",
                "--json",
                "--report",
                "--observations-dir",
                str(observations_dir),
            ],
        )

    assert result.exit_code == 3
    assert seen_argv == [
        "--scenario",
        "001-replication-lag",
        "--parallel-levels",
        "4",
        "--json",
        "--mock-grafana",
        "--report",
        "--observations-dir",
        str(observations_dir),
    ]


def test_tests_synthetic_cli_does_not_pass_observations_dir_when_unset(tmp_path: Path) -> None:
    runner = CliRunner()
    scenarios_dir = tmp_path / "rds_postgres"
    (scenarios_dir / "001-replication-lag").mkdir(parents=True)

    seen_argv: list[str] = []

    def _fake_main(argv: list[str]) -> int:
        seen_argv[:] = argv
        return 0

    fake_run_suite = types.ModuleType("tests.synthetic.rds_postgres.run_suite")
    fake_run_suite.main = _fake_main

    with (
        unittest.mock.patch("app.cli.tests.discover.SYNTHETIC_SCENARIOS_DIR", scenarios_dir),
        unittest.mock.patch.dict(
            sys.modules,
            {"tests.synthetic.rds_postgres.run_suite": fake_run_suite},
        ),
    ):
        result = runner.invoke(
            cli,
            [
                "tests",
                "synthetic",
                "--json",
                "--no-report",
            ],
        )

    assert result.exit_code == 0
    assert seen_argv == ["--json", "--mock-grafana", "--no-report"]


def test_tests_synthetic_all_defaults_to_parallel_all_levels(tmp_path: Path) -> None:
    runner = CliRunner()
    scenarios_dir = tmp_path / "rds_postgres"
    (scenarios_dir / "001-replication-lag").mkdir(parents=True)

    seen_argv: list[str] = []

    def _fake_main(argv: list[str]) -> int:
        seen_argv[:] = argv
        return 0

    fake_run_suite = types.ModuleType("tests.synthetic.rds_postgres.run_suite")
    fake_run_suite.main = _fake_main

    with (
        unittest.mock.patch("app.cli.tests.discover.SYNTHETIC_SCENARIOS_DIR", scenarios_dir),
        unittest.mock.patch.dict(
            sys.modules,
            {"tests.synthetic.rds_postgres.run_suite": fake_run_suite},
        ),
    ):
        result = runner.invoke(
            cli,
            [
                "tests",
                "synthetic",
                "all",
            ],
        )

    assert result.exit_code == 0
    assert seen_argv == ["--parallel-levels", "4", "--mock-grafana"]


def test_tests_openclaw_synthetic_cli_forwards_flags_to_run_suite_main(tmp_path: Path) -> None:
    runner = CliRunner()
    scenarios_dir = tmp_path / "openclaw" / "scenarios"
    (scenarios_dir / "gateway_process_terminated_missing_tls_key").mkdir(parents=True)

    seen_argv: list[str] = []

    def _fake_main(argv: list[str]) -> int:
        seen_argv[:] = argv
        return 2

    fake_run_suite = types.ModuleType("tests.synthetic.openclaw.run_suite")
    fake_run_suite.main = _fake_main

    with (
        unittest.mock.patch(
            "app.cli.tests.discover.OPENCLAW_SYNTHETIC_SCENARIOS_DIR",
            scenarios_dir,
        ),
        unittest.mock.patch.dict(
            sys.modules,
            {"tests.synthetic.openclaw.run_suite": fake_run_suite},
        ),
    ):
        result = runner.invoke(
            cli,
            [
                "tests",
                "openclaw-synthetic",
                "--scenario",
                "gateway_process_terminated_missing_tls_key",
                "--json",
            ],
        )

    assert result.exit_code == 2
    assert seen_argv == ["--scenario", "gateway_process_terminated_missing_tls_key", "--json"]
