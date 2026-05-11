"""Thin orchestration entrypoint for the synthetic RDS PostgreSQL benchmark suite.

Pure scoring logic lives in scoring.py.
Rendering/cross-axis reports live in reporting.py.
Per-scenario observation building and Rich rendering live in observations.py.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import textwrap
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from tests.synthetic.mock_aws_backend import FixtureAWSBackend
from tests.synthetic.mock_grafana_backend.backend import FixtureGrafanaBackend
from tests.synthetic.rds_postgres.observations import (
    TrajectoryPolicy,
    TrajectoryPolicyResult,
    build_observation,
    compute_trajectory_metrics,
    evaluate_trajectory_policy,
    render_report_to_console,
    write_observation,
)
from tests.synthetic.rds_postgres.runner_api import (
    LevelRunConfig,
    LevelRunResult,
    SuiteRunConfig,
    SuiteRunResult,
    group_fixtures_by_level,
    parse_levels_csv,
    select_fixtures,
)
from tests.synthetic.rds_postgres.scenario_loader import (
    SUITE_DIR,
    GoldenTrajectoryConfig,
    ScenarioFixture,
    load_all_scenarios,
)

# Re-export scoring symbols so existing import sites continue to work without
# modification. Track removal in a follow-up once all sites are migrated.
from tests.synthetic.rds_postgres.scoring import (
    FailureDetail,
    GateResult,
    ReasoningScore,
    ScenarioScore,
    TrajectoryScore,
    _all_required_gates_pass,
    score_reasoning,
    score_result,
    score_trajectory,
)

__all__ = [
    # dataclasses
    "FailureDetail",
    "GateResult",
    "ReasoningScore",
    "ScenarioScore",
    "TrajectoryScore",
    # functions
    "score_result",
    "score_reasoning",
    "score_trajectory",
    # orchestration
    "run_scenario",
    "run_synthetic_suite",
    "run_suite",
    "main",
]


def _run_investigation_lazy(**kwargs: Any) -> Any:
    from app.pipeline.runners import run_investigation as _run_investigation

    return _run_investigation(**kwargs)


# Keep as module symbol so tests can monkeypatch without importing heavy deps.
run_investigation: Callable[..., Any] = _run_investigation_lazy


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the synthetic RDS PostgreSQL RCA suite.")
    parser.add_argument(
        "--scenario",
        default="",
        help="Run a single scenario directory name, e.g. 001-replication-lag.",
    )
    parser.add_argument(
        "--levels",
        default="1,2,3,4",
        help=(
            "Comma-separated scenario_difficulty levels to execute (1-4). "
            "Ignored when --scenario is set."
        ),
    )
    parser.add_argument(
        "--parallel-levels",
        type=int,
        default=1,
        dest="parallel_levels",
        help="Number of scenario levels to execute in parallel (max 4).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON results.",
    )
    parser.add_argument(
        "--mock-grafana",
        action="store_true",
        dest="mock_grafana",
        help="Serve fixture data via FixtureGrafanaBackend instead of real Grafana calls.",
    )
    parser.add_argument(
        "--axis2",
        action="store_true",
        help="Print Axis 1 vs Axis 2 gap report (requires results from both suites).",
    )
    report_group = parser.add_mutually_exclusive_group()
    report_group.add_argument(
        "--report",
        action="store_true",
        dest="report",
        help="Print Rich observation report per scenario.",
    )
    report_group.add_argument(
        "--no-report",
        action="store_false",
        dest="report",
        help="Disable Rich observation report output.",
    )
    parser.set_defaults(report=None)
    parser.add_argument(
        "--observations-dir",
        default=str(SUITE_DIR / "_observations"),
        help="Directory where per-run observation JSON files are written.",
    )
    parser.add_argument(
        "--baseline-out",
        default="",
        dest="baseline_out",
        help="Write per-scenario canonical_report_payload JSON snapshots into this directory.",
    )
    parser.add_argument(
        "--baseline-check",
        default="",
        dest="baseline_check",
        help=(
            "Compare each scenario's canonical_report_payload against snapshots in this "
            "directory. Exits non-zero on any mismatch."
        ),
    )
    return parser.parse_args(argv)


def _build_run_config(args: argparse.Namespace) -> SuiteRunConfig:
    return SuiteRunConfig(
        scenario=str(args.scenario or "").strip(),
        levels=parse_levels_csv(args.levels),
        parallel_levels=max(1, int(args.parallel_levels)),
        output_json=bool(args.json),
        mock_grafana=bool(args.mock_grafana),
        report=args.report,
        observations_dir=Path(args.observations_dir),
        baseline_out=Path(args.baseline_out) if args.baseline_out else None,
        baseline_check=Path(args.baseline_check) if args.baseline_check else None,
    )


def _build_resolved_integrations(
    fixture: ScenarioFixture,
    use_mock_grafana: bool,
    grafana_backend: Any = None,
) -> dict[str, Any] | None:
    """Build pre-resolved integrations for injection into run_investigation."""
    integrations: dict[str, Any] = {}
    if use_mock_grafana or grafana_backend is not None:
        integrations["grafana"] = {
            "endpoint": "",
            "api_key": "",
            "_backend": grafana_backend or FixtureGrafanaBackend(fixture),
        }
    integrations["aws"] = {
        "region": fixture.metadata.region,
        "ec2_backend": FixtureAWSBackend(fixture),
    }
    return integrations


def _resolved_golden_trajectory(
    fixture: ScenarioFixture,
) -> tuple[list[str], int | None, GoldenTrajectoryConfig | None]:
    golden_cfg = fixture.answer_key.golden_trajectory
    if golden_cfg is not None and golden_cfg.ordered_actions:
        if golden_cfg.max_loops is not None:
            return list(golden_cfg.ordered_actions), golden_cfg.max_loops, golden_cfg
        return (
            list(golden_cfg.ordered_actions),
            fixture.answer_key.max_investigation_loops,
            golden_cfg,
        )
    return (
        list(fixture.answer_key.optimal_trajectory),
        fixture.answer_key.max_investigation_loops,
        None,
    )


def _trajectory_policy_for_fixture(
    *,
    max_loops: int | None,
    golden_cfg: GoldenTrajectoryConfig | None,
) -> TrajectoryPolicy | None:
    if golden_cfg is None:
        return None
    return TrajectoryPolicy(
        matching=golden_cfg.matching,
        max_edit_distance=golden_cfg.max_edit_distance,
        max_extra_actions=golden_cfg.max_extra_actions,
        max_redundancy=golden_cfg.max_redundancy,
        max_loops=max_loops,
    )


def _apply_trajectory_policy_to_score(
    score: ScenarioScore,
    trajectory_policy: TrajectoryPolicyResult | None,
) -> ScenarioScore:
    """Apply the trajectory policy result to the score, always recording the gate.

    The gate is recorded in ALL cases (pass, fail, not-applicable) so that
    ``_all_required_gates_pass`` acts as a true hard gate.
    """
    gates = dict(score.gates)

    if trajectory_policy is None:
        gates["trajectory_policy"] = GateResult(
            status="pass",
            threshold="not_applicable — no golden trajectory configured",
            actual="not_applicable",
        )
        return replace(
            score,
            passed=_all_required_gates_pass(gates) and not score.failure_reasons,
            gates=gates,
        )

    gates["trajectory_policy"] = GateResult(
        status="pass" if trajectory_policy.passed else "fail",
        threshold="policy violations list must be empty",
        actual=f"violations={trajectory_policy.violations}",
    )

    if trajectory_policy.passed:
        return replace(
            score,
            passed=_all_required_gates_pass(gates) and not score.failure_reasons,
            gates=gates,
        )

    policy_reason = "trajectory policy failed: " + "; ".join(
        trajectory_policy.violations or ["unknown violation"]
    )
    failures = list(score.failure_reasons)
    if not any(detail.code == "TRAJECTORY_POLICY_FAILED" for detail in failures):
        failures.append(FailureDetail(code="TRAJECTORY_POLICY_FAILED", detail=policy_reason))

    combined_reason = "; ".join(detail.detail for detail in failures)

    return replace(
        score,
        passed=_all_required_gates_pass(gates) and not failures,
        gates=gates,
        failure_reasons=failures,
        failure_reason=combined_reason,
    )


def run_scenario(
    fixture: ScenarioFixture,
    use_mock_grafana: bool = False,
    grafana_backend: Any = None,
) -> tuple[dict[str, Any], ScenarioScore]:
    alert = fixture.alert
    labels = alert.get("commonLabels", {}) or {}

    alert_name = str(alert.get("title") or labels.get("alertname") or fixture.scenario_id)
    pipeline_name = str(labels.get("pipeline_name") or "rds-postgres-synthetic")
    severity = str(labels.get("severity") or "critical")

    resolved_integrations = _build_resolved_integrations(
        fixture, use_mock_grafana, grafana_backend=grafana_backend
    )

    final_state = run_investigation(
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=alert,
        resolved_integrations=resolved_integrations,
    )
    state_dict = dict(final_state)

    queried_metrics: list[str] | None = None
    if grafana_backend is not None and hasattr(grafana_backend, "queried_metrics"):
        queried_metrics = list(grafana_backend.queried_metrics)

    return state_dict, score_result(fixture, state_dict, queried_metrics=queried_metrics)


@dataclass(frozen=True)
class _ScenarioExecution:
    fixture: ScenarioFixture
    score: ScenarioScore
    canonical_report_payload: dict[str, Any]
    observation_for_report: Any
    wall_time_s: float


def _execute_fixture(
    fixture: ScenarioFixture,
    *,
    config: SuiteRunConfig,
    progress_hook: Callable[[str, int], None] | None = None,
) -> _ScenarioExecution:
    if progress_hook is not None:
        progress_hook(fixture.scenario_id, 1)
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    final_state, score = run_scenario(fixture, use_mock_grafana=config.mock_grafana)
    wall_time_s = time.monotonic() - started_monotonic
    if progress_hook is not None:
        progress_hook(fixture.scenario_id, 2)

    executed_hypotheses = final_state.get("executed_hypotheses") or []
    loops_used = len(executed_hypotheses)
    golden_trajectory, max_loops, golden_cfg = _resolved_golden_trajectory(fixture)
    trajectory_metrics = compute_trajectory_metrics(
        executed_hypotheses=executed_hypotheses,
        golden=golden_trajectory,
        loops_used=loops_used,
        max_loops=max_loops,
    )
    trajectory_policy = (
        evaluate_trajectory_policy(
            metrics=trajectory_metrics,
            golden_actions=golden_trajectory,
            policy=_trajectory_policy_for_fixture(
                max_loops=max_loops,
                golden_cfg=golden_cfg,
            ),
        )
        if golden_cfg is not None
        else None
    )

    score = _apply_trajectory_policy_to_score(score, trajectory_policy)
    if progress_hook is not None:
        progress_hook(fixture.scenario_id, 3)

    observation = build_observation(
        scenario_id=fixture.scenario_id,
        suite="axis1",
        backend="FixtureGrafanaBackend" if config.mock_grafana else "LiveGrafanaBackend",
        score=asdict(score),
        reasoning=asdict(score.reasoning) if score.reasoning is not None else None,
        trajectory=trajectory_metrics,
        evaluated_golden_actions=golden_trajectory,
        trajectory_policy=trajectory_policy,
        final_state=final_state,
        available_evidence_sources=list(fixture.metadata.available_evidence),
        required_evidence_sources=list(fixture.answer_key.required_evidence_sources),
        started_at=started_at,
        wall_time_s=wall_time_s,
    )

    observation_path = write_observation(observation, config.observations_dir)
    relative_observation_path = str(observation_path.relative_to(config.observations_dir))
    display_observation_path = str(observation_path.resolve())
    observation_for_report = replace(
        observation,
        observation_path=f"{relative_observation_path} ({display_observation_path})",
    )
    if progress_hook is not None:
        progress_hook(fixture.scenario_id, 4)

    return _ScenarioExecution(
        fixture=fixture,
        score=score,
        canonical_report_payload=observation.canonical_report_payload,
        observation_for_report=observation_for_report,
        wall_time_s=wall_time_s,
    )


def _run_level(
    level_config: LevelRunConfig,
    *,
    config: SuiteRunConfig,
    progress_hook: Callable[[str, int], None] | None = None,
) -> tuple[list[_ScenarioExecution], LevelRunResult]:
    started = time.monotonic()
    executions: list[_ScenarioExecution] = []
    for fixture in level_config.fixtures:
        executions.append(_execute_fixture(fixture, config=config, progress_hook=progress_hook))

    passed = sum(1 for execution in executions if execution.score.passed)
    level_result = LevelRunResult(
        level=level_config.level,
        scenario_ids=tuple(execution.fixture.scenario_id for execution in executions),
        passed=passed,
        failed=len(executions) - passed,
        wall_time_s=time.monotonic() - started,
    )
    return executions, level_result


@contextmanager
def _suppress_investigation_rendering(enabled: bool) -> Iterator[None]:
    """Temporarily disable node-level investigation rendering."""
    if not enabled:
        yield
        return

    previous_output_format = os.environ.get("TRACER_OUTPUT_FORMAT")
    os.environ["TRACER_OUTPUT_FORMAT"] = "none"

    from app import output as output_module

    output_module.get_tracker(reset=True)
    try:
        yield
    finally:
        if previous_output_format is None:
            os.environ.pop("TRACER_OUTPUT_FORMAT", None)
        else:
            os.environ["TRACER_OUTPUT_FORMAT"] = previous_output_format
        output_module.get_tracker(reset=True)


def _render_suite_overview(
    console: Console,
    *,
    config: SuiteRunConfig,
    level_configs: tuple[LevelRunConfig, ...],
) -> None:
    total = sum(len(level.fixtures) for level in level_configs)
    overview = Table(title="Synthetic Suite Overview", show_header=True)
    overview.add_column("Level", justify="right")
    overview.add_column("Scenarios", justify="right")
    overview.add_column("IDs")
    for level in level_configs:
        scenario_ids = ", ".join(fixture.scenario_id for fixture in level.fixtures)
        overview.add_row(str(level.level), str(len(level.fixtures)), scenario_ids)
    console.print(overview)
    console.print(
        "Run config: "
        f"total={total}, parallel_levels={config.parallel_levels}, "
        f"mock_grafana={config.mock_grafana}, observations_dir={config.observations_dir}"
    )


def _render_suite_summary(
    console: Console,
    *,
    executions: list[_ScenarioExecution],
    level_results: tuple[LevelRunResult, ...],
) -> None:
    summary = Table(title="Synthetic Suite Report", show_header=True)
    summary.add_column("Scenario")
    summary.add_column("Level", justify="right")
    summary.add_column("Status")
    summary.add_column("Category")
    summary.add_column("Wall(s)", justify="right")
    summary.add_column("Detail")

    for execution in executions:
        status = "PASS" if execution.score.passed else "FAIL"
        detail = execution.score.failure_reason or "-"
        summary.add_row(
            execution.fixture.scenario_id,
            str(execution.fixture.metadata.scenario_difficulty),
            status,
            execution.score.actual_category,
            f"{execution.wall_time_s:.2f}",
            detail,
        )
    console.print(summary)

    level_table = Table(title="Level Summary", show_header=True)
    level_table.add_column("Level", justify="right")
    level_table.add_column("Passed", justify="right")
    level_table.add_column("Failed", justify="right")
    level_table.add_column("Wall(s)", justify="right")
    for level_result in level_results:
        level_table.add_row(
            str(level_result.level),
            str(level_result.passed),
            str(level_result.failed),
            f"{level_result.wall_time_s:.2f}",
        )
    console.print(level_table)


def _write_baseline(canonical_payloads: dict[str, Any], baseline_out_dir: Path) -> None:
    """Write per-scenario canonical_report_payload snapshots to *baseline_out_dir*."""
    baseline_out_dir.mkdir(parents=True, exist_ok=True)
    for scenario_id, payload in canonical_payloads.items():
        target = baseline_out_dir / f"{scenario_id}.json"
        target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _check_baseline(
    canonical_payloads: dict[str, Any],
    baseline_check_dir: Path,
) -> list[str]:
    """Compare canonical payloads against committed baseline snapshots.

    Returns a list of human-readable mismatch descriptions (empty if all match).
    """
    mismatches: list[str] = []
    for scenario_id, actual_payload in canonical_payloads.items():
        baseline_file = baseline_check_dir / f"{scenario_id}.json"
        if not baseline_file.exists():
            mismatches.append(f"{scenario_id}: baseline file missing at {baseline_file}")
            continue
        expected = json.loads(baseline_file.read_text(encoding="utf-8"))
        actual_canonical = json.loads(
            json.dumps(actual_payload, sort_keys=True, separators=(",", ":"))
        )
        expected_canonical = json.loads(json.dumps(expected, sort_keys=True, separators=(",", ":")))
        if actual_canonical != expected_canonical:
            actual_str = json.dumps(actual_payload, indent=2, sort_keys=True)
            expected_str = json.dumps(expected, indent=2, sort_keys=True)
            diff_lines: list[str] = []
            for line in difflib.unified_diff(
                expected_str.splitlines(),
                actual_str.splitlines(),
                fromfile=f"{scenario_id} (baseline)",
                tofile=f"{scenario_id} (actual)",
                lineterm="",
            ):
                diff_lines.append(line)
            mismatches.append(
                f"{scenario_id}: canonical payload differs from baseline\n"
                + "\n".join(diff_lines[:60])
            )
    return mismatches


def run_synthetic_suite(config: SuiteRunConfig) -> SuiteRunResult:
    fixtures = load_all_scenarios(SUITE_DIR)
    try:
        selected_fixtures = select_fixtures(fixtures, config)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    level_order = (
        tuple(sorted({fixture.metadata.scenario_difficulty for fixture in selected_fixtures}))
        if config.scenario
        else config.levels
    )
    level_configs = group_fixtures_by_level(selected_fixtures, level_order)
    interactive_console = Console(highlight=False, soft_wrap=True)
    show_interactive = not config.output_json
    bulk_run = len(selected_fixtures) > 1
    show_overview_only = show_interactive and bulk_run
    if show_interactive and level_configs:
        _render_suite_overview(interactive_console, config=config, level_configs=level_configs)

    level_executions: dict[int, list[_ScenarioExecution]] = {}
    level_results_map: dict[int, LevelRunResult] = {}
    task_map: dict[str, TaskID] = {}
    progress: Progress | None = None
    if show_interactive and level_configs and not show_overview_only:
        progress = Progress(
            TextColumn("[bold blue]{task.fields[level]}[/bold blue]"),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=interactive_console,
            transient=False,
        )
        for level_config in level_configs:
            for fixture in level_config.fixtures:
                task_id = progress.add_task(
                    description=fixture.scenario_id,
                    total=4,
                    completed=0,
                    level=f"L{level_config.level}",
                )
                task_map[fixture.scenario_id] = task_id

    def _progress_hook(scenario_id: str, step: int) -> None:
        if progress is None:
            return
        task_id = task_map.get(scenario_id)
        if task_id is None:
            return
        progress.update(task_id, completed=step)

    max_workers = min(config.parallel_levels, len(level_configs)) if level_configs else 1
    progress_context = progress if progress is not None else nullcontext()
    suppress_investigation_rendering = bulk_run or config.output_json
    with (
        _suppress_investigation_rendering(suppress_investigation_rendering),
        progress_context,
    ):
        if max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    level_config.level: executor.submit(
                        _run_level,
                        level_config,
                        config=config,
                        progress_hook=_progress_hook,
                    )
                    for level_config in level_configs
                }
                for level, future in futures.items():
                    executions, level_result = future.result()
                    level_executions[level] = executions
                    level_results_map[level] = level_result
        else:
            for level_config in level_configs:
                executions, level_result = _run_level(
                    level_config,
                    config=config,
                    progress_hook=_progress_hook,
                )
                level_executions[level_config.level] = executions
                level_results_map[level_config.level] = level_result

    ordered_executions: list[_ScenarioExecution] = []
    ordered_level_results: list[LevelRunResult] = []
    for level in level_order:
        if level in level_results_map:
            ordered_executions.extend(level_executions[level])
            ordered_level_results.append(level_results_map[level])

    should_report = (
        bool(config.report) if config.report is not None else len(selected_fixtures) == 1
    )
    if config.output_json:
        should_report = False

    if should_report:
        report_console = (
            interactive_console if show_interactive else Console(highlight=False, soft_wrap=True)
        )
        for execution in ordered_executions:
            render_report_to_console(execution.observation_for_report, report_console)

    if show_interactive and ordered_executions and not show_overview_only:
        _render_suite_summary(
            interactive_console,
            executions=ordered_executions,
            level_results=tuple(ordered_level_results),
        )

    return SuiteRunResult(
        config=config,
        level_results=tuple(ordered_level_results),
        scores=tuple(execution.score for execution in ordered_executions),
        canonical_payloads={
            execution.fixture.scenario_id: execution.canonical_report_payload
            for execution in ordered_executions
        },
    )


def run_suite(argv: list[str] | None = None) -> list[ScenarioScore]:
    args = parse_args(argv)
    config = _build_run_config(args)
    suite_result = run_synthetic_suite(config)
    results = list(suite_result.scores)
    canonical_payloads = dict(suite_result.canonical_payloads)

    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))

    if config.baseline_out:
        _write_baseline(canonical_payloads, config.baseline_out)

    if config.baseline_check:
        mismatches = _check_baseline(canonical_payloads, config.baseline_check)
        if mismatches:
            print("\n=== Baseline Check FAILED ===")
            for msg in mismatches:
                print(textwrap.indent(msg, "  "))
            raise SystemExit(1)

    return results


def main(argv: list[str] | None = None) -> int:
    results = run_suite(argv)
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
