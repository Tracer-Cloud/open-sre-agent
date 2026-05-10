"""Test catalog CLI commands."""

from __future__ import annotations

import json
from typing import Any

import click

from app.analytics.cli import (
    capture_test_run_completed,
    capture_test_run_failed,
    capture_test_run_started,
    capture_test_synthetic_completed,
    capture_test_synthetic_failed,
    capture_test_synthetic_started,
    capture_tests_listed,
    capture_tests_picker_opened,
)
from app.cli.support.context import is_json_output, is_yes
from app.cli.support.errors import OpenSREError

_TEST_CATEGORIES: tuple[str, ...] = ("all", "rca", "synthetic", "demo", "infra-heavy", "ci-safe")


class _TestIdType(click.ParamType):
    """Click parameter type that provides dynamic shell completion for test IDs."""

    name = "test_id"

    def shell_complete(
        self,
        _ctx: click.Context,
        _param: click.Parameter,
        incomplete: str,
    ) -> list[click.shell_completion.CompletionItem]:
        try:
            from app.cli.tests.discover import load_test_catalog

            catalog = load_test_catalog()
            return [
                click.shell_completion.CompletionItem(item.id)
                for item in catalog.all_items()
                if item.id.startswith(incomplete) and item.is_runnable
            ]
        except Exception:
            return []


def _echo_catalog_item(item: Any, *, indent: int = 0) -> None:
    prefix = "  " * indent
    tag_text = f" [{', '.join(item.tags)}]" if item.tags else ""
    click.echo(f"{prefix}{item.id} - {item.display_name}{tag_text}")
    if item.description:
        click.echo(f"{prefix}  {item.description}")
    for child in item.children:
        _echo_catalog_item(child, indent=indent + 1)


def _build_synthetic_argv(
    *,
    scenario: str,
    output_json: bool,
    mock_grafana: bool,
    report: bool | None,
    observations_dir: str,
) -> list[str]:
    argv: list[str] = []
    if scenario:
        argv.extend(["--scenario", scenario])
    if output_json:
        argv.append("--json")
    if mock_grafana:
        argv.append("--mock-grafana")
    if report is True:
        argv.append("--report")
    elif report is False:
        argv.append("--no-report")
    if observations_dir:
        argv.extend(["--observations-dir", observations_dir])
    return argv


def _build_cloudopsbench_argv(
    *,
    system: str,
    fault_category: str,
    case: str,
    limit: int,
    workers: int,
    output_json: bool,
) -> list[str]:
    argv: list[str] = []
    if system:
        argv.extend(["--system", system])
    if fault_category:
        argv.extend(["--fault-category", fault_category])
    if case:
        argv.extend(["--case", case])
    if limit:
        argv.extend(["--limit", str(limit)])
    if workers != 1:
        argv.extend(["--workers", str(workers)])
    if output_json:
        argv.append("--json")
    return argv


@click.group(name="tests", invoke_without_command=True)
@click.pass_context
def tests(ctx: click.Context) -> None:
    """Browse and run inventoried tests from the terminal."""
    if ctx.invoked_subcommand is not None:
        return

    if is_yes() or is_json_output():
        raise OpenSREError(
            "No subcommand provided.",
            suggestion="Run 'opensre tests list' or 'opensre tests run <test_id>'.",
        )

    from app.cli.tests.discover import load_test_catalog
    from app.cli.tests.interactive import run_interactive_picker

    catalog = load_test_catalog()
    capture_tests_picker_opened()
    try:
        exit_code = run_interactive_picker(catalog)
    except RuntimeError as exc:
        raise OpenSREError(
            str(exc),
            suggestion="Run 'opensre tests list' or 'opensre tests run <test_id>'.",
        ) from exc
    raise SystemExit(exit_code)


def _synthetic_suite_not_bundled_error() -> OpenSREError:
    """Structured error for ``opensre tests synthetic`` when the suite isn't shipped."""
    return OpenSREError(
        "The synthetic RDS PostgreSQL suite is not available in this build.",
        suggestion=(
            "Pre-built binaries do not bundle the per-scenario data files "
            "under 'tests/synthetic/rds_postgres/'. Install from source "
            "(`git clone https://github.com/Tracer-Cloud/opensre && pip "
            "install -e .`) and re-run 'opensre tests synthetic'."
        ),
    )


@tests.command(name="synthetic")
@click.option(
    "--scenario", default="", help="Pin to a single scenario directory, e.g. 001-replication-lag."
)
@click.option("--json", "output_json", is_flag=True, help="Print machine-readable JSON results.")
@click.option(
    "--mock-grafana",
    is_flag=True,
    default=True,
    show_default=True,
    help="Serve fixture data via FixtureGrafanaBackend instead of real Grafana calls.",
)
@click.option(
    "--report/--no-report",
    default=None,
    help=(
        "Print Rich observation report per scenario. Defaults to auto "
        "(enabled for single-scenario runs)."
    ),
)
@click.option(
    "--observations-dir",
    default="",
    help="Directory where synthetic run observations are written.",
)
def run_synthetic_suite(
    scenario: str,
    output_json: bool,
    mock_grafana: bool,
    report: bool | None,
    observations_dir: str,
) -> None:
    """Run the synthetic RDS PostgreSQL RCA benchmark."""
    # ``packaging/opensre.spec`` only collects ``app/`` data files, so neither
    # the synthetic Python package's submodules nor the per-scenario data
    # directories are reliably present in PyInstaller bundles. Two failure
    # modes can trip a bundled binary here:
    #
    # 1. The ``tests.synthetic.rds_postgres.*`` Python package is missing
    #    entirely  →  ``ModuleNotFoundError`` raised at import time.
    # 2. The package is included transitively but its data dir
    #    (``tests/synthetic/rds_postgres/<scenario>/``) is absent
    #    →  ``run_suite`` crashes later with ``FileNotFoundError`` from
    #    ``Path.iterdir()`` inside the scenario loader.
    #
    # We pre-check the data dir explicitly *and* catch a narrow
    # ``ModuleNotFoundError`` so users see one structured message regardless
    # of which failure mode their bundle produces. The data-dir path is the
    # ``SYNTHETIC_SCENARIOS_DIR`` constant from ``discover.py`` — single
    # source of truth shared with ``_discover_rds_synthetic_scenarios``.
    from app.cli.tests.discover import SYNTHETIC_SCENARIOS_DIR

    if not SYNTHETIC_SCENARIOS_DIR.is_dir():
        raise _synthetic_suite_not_bundled_error()

    try:
        from tests.synthetic.rds_postgres.run_suite import main as run_suite_main
    except ModuleNotFoundError as exc:
        # Narrow to the actual missing-bundle case; re-raise unrelated import
        # failures (e.g. a missing transitive dep like ``psycopg``) so users
        # see the real cause instead of a misleading "not bundled" message.
        if exc.name is None or not exc.name.startswith("tests.synthetic.rds_postgres"):
            raise
        raise _synthetic_suite_not_bundled_error() from exc

    capture_test_synthetic_started(scenario or "all", mock_grafana=mock_grafana)
    scenario_name = scenario or "all"
    try:
        exit_code = run_suite_main(
            _build_synthetic_argv(
                scenario=scenario,
                output_json=output_json,
                mock_grafana=mock_grafana,
                report=report,
                observations_dir=observations_dir,
            )
        )
    except Exception as exc:
        capture_test_synthetic_failed(scenario_name, reason=type(exc).__name__)
        raise

    capture_test_synthetic_completed(scenario_name, exit_code=exit_code)
    raise SystemExit(exit_code)


def _cloudopsbench_suite_not_bundled_error() -> OpenSREError:
    return OpenSREError(
        "The Cloud-OpsBench suite is not available in this build.",
        suggestion=(
            "Download the corpus with 'make download-cloudopsbench-hf' under "
            "'tests/benchmarks/cloudopsbench/benchmark/' and re-run "
            "'opensre tests cloudopsbench'."
        ),
    )


@tests.command(name="cloudopsbench")
@click.option("--system", default="", help="Filter to boutique or trainticket.")
@click.option("--fault-category", default="", help="Filter to one CloudOps fault category.")
@click.option("--case", "case_name", default="", help="Filter to one numeric case directory.")
@click.option("--limit", default=0, type=int, help="Limit cases after sorting/filtering.")
@click.option("--workers", default=1, type=int, show_default=True, help="Number of case workers.")
@click.option("--json", "output_json", is_flag=True, help="Print machine-readable JSON results.")
def run_cloudopsbench_suite(
    system: str,
    fault_category: str,
    case_name: str,
    limit: int,
    workers: int,
    output_json: bool,
) -> None:
    """Run the Cloud-OpsBench RCA benchmark through OpenSRE."""
    try:
        from tests.benchmarks.cloudopsbench.case_loader import BENCHMARK_DIR
        from tests.benchmarks.cloudopsbench.run_suite import main as run_suite_main
    except ModuleNotFoundError as exc:
        if exc.name is None or not exc.name.startswith("tests.benchmarks.cloudopsbench"):
            raise
        raise _cloudopsbench_suite_not_bundled_error() from exc

    if not BENCHMARK_DIR.is_dir():
        raise _cloudopsbench_suite_not_bundled_error()

    raise SystemExit(
        run_suite_main(
            _build_cloudopsbench_argv(
                system=system,
                fault_category=fault_category,
                case=case_name,
                limit=limit,
                workers=workers,
                output_json=output_json,
            )
        )
    )


def _catalog_item_to_dict(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.display_name,
        "tags": list(item.tags) if item.tags else [],
        "description": item.description or "",
        "children": [_catalog_item_to_dict(c) for c in item.children],
    }


@tests.command(name="list")
@click.option(
    "--category",
    type=click.Choice(_TEST_CATEGORIES),
    default="all",
    show_default=True,
    help="Filter the inventory by category tag.",
)
@click.option("--search", default="", help="Case-insensitive text filter.")
def list_tests(category: str, search: str) -> None:
    """List available tests and suites."""
    from app.cli.tests.discover import load_test_catalog

    capture_tests_listed(category, search=bool(search))

    catalog = load_test_catalog()
    items = list(catalog.filter(category=category, search=search))

    if is_json_output():
        click.echo(json.dumps([_catalog_item_to_dict(i) for i in items], indent=2))
        return

    for item in items:
        _echo_catalog_item(item)


@tests.command(name="run")
@click.argument("test_id", type=_TestIdType())
@click.option("--dry-run", is_flag=True, help="Print the selected command without running it.")
def run_test(test_id: str, dry_run: bool) -> None:
    """Run a test or suite by stable inventory id."""
    from app.cli.tests.runner import find_test_item, run_catalog_item

    item = find_test_item(test_id)
    if item is None:
        raise OpenSREError(
            f"Unknown test id: '{test_id}'.",
            suggestion="Run 'opensre tests list' to see available test ids.",
        )
    if not item.is_runnable:
        raise OpenSREError(
            f"Test '{test_id}' is a suite and cannot be run directly.",
            suggestion="Run 'opensre tests list' to see individual runnable ids.",
        )

    capture_test_run_started(test_id, dry_run=dry_run)
    try:
        exit_code = run_catalog_item(item, dry_run=dry_run)
    except Exception as exc:
        capture_test_run_failed(test_id, dry_run=dry_run, reason=type(exc).__name__)
        raise
    if exit_code == 0:
        capture_test_run_completed(test_id, dry_run=dry_run, exit_code=exit_code)
    else:
        capture_test_run_failed(test_id, dry_run=dry_run, reason=f"exit_code_{exit_code}")
    raise SystemExit(exit_code)
