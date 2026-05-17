"""Benchmark orchestrator — wires Config + Adapter + IntegrityGuard + CostTracker.

Runs the (case × mode × llm × run) grid serially for v1; parallel workers
land in v1.1 once the serial path is verified end-to-end.

Two entry points:

  - ``BenchmarkRunner.run()`` — production. Enforces all integrity gates,
    refuses to start without pre-registration + validity metrics + seeded
    selection; refuses to emit a report without per-stratum breakdown +
    negative-results + COI.

  - ``BenchmarkRunner.run_without_integrity()`` — DEVELOPMENT ONLY. Skips
    integrity gates so the rest of the wiring can be smoke-tested before
    Phase C (validity metrics) and Phase D (seen/unseen tagging) ship.
    Stamps results with ``dev_mode=True`` so they can't be silently
    promoted to a real report.

opensre+LLM mode wires opensre's ``run_investigation`` against the adapter's
integrations. ``llm_alone`` mode is Phase B; ``run()`` raises if requested.

llm_dispatch is not yet implemented — the runner uses whatever LLM opensre
is configured with via env vars. ``RunResult.model_version`` is set to
``"(unpinned)"`` accordingly; a future llm_dispatch.py will enable per-cell
model selection with version pinning.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tests.benchmarks._framework.adapters import (
    BenchmarkAdapter,
    BenchmarkCase,
    CaseFilters,
    CaseScore,
    Mode,
    RunResult,
)
from tests.benchmarks._framework.config import BenchmarkConfig
from tests.benchmarks._framework.cost import CostBudgetExceeded, CostTracker
from tests.benchmarks._framework.integrity import (
    BenchmarkReport,
    IntegrityGuard,
    make_baseline_report,
)

# --------------------------------------------------------------------------- #
# Internal types                                                              #
# --------------------------------------------------------------------------- #


@dataclass
class _CellResult:
    """One scenario × mode × llm × run cell with run + score + on-disk path."""

    case: BenchmarkCase
    mode: Mode
    llm: str
    run_index: int
    run: RunResult
    score: CaseScore
    artifact_path: Path


@dataclass
class RunOutcome:
    """What ``run()`` returns: the report + the cell-by-cell results."""

    report: BenchmarkReport
    cells: list[_CellResult] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str | None = None


# --------------------------------------------------------------------------- #
# BenchmarkRunner                                                             #
# --------------------------------------------------------------------------- #


class BenchmarkRunner:
    """Drives a single benchmark run end-to-end.

    v1 limitations (will lift as later modules ship):
      - Serial execution (parallel comes when worker-pool tested)
      - opensre+llm mode only (llm_alone is Phase B)
      - No per-cell LLM dispatch (uses opensre's configured LLM)
      - Stratum reporting is `all` only until Phase D tagging adds seen/unseen
    """

    def __init__(
        self,
        config: BenchmarkConfig,
        adapter: BenchmarkAdapter,
        integrity_guard: IntegrityGuard | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.integrity = integrity_guard or IntegrityGuard()
        self.cost = cost_tracker or CostTracker(budget_usd=config.cost_budget_usd)
        self._opensre_sha = _git_sha()

    # ----------------------------------------------------------------------- #
    # Public API                                                              #
    # ----------------------------------------------------------------------- #

    def run(self) -> RunOutcome:
        """Production entry point: enforces all integrity gates."""
        self.integrity.pre_flight(self.config, self.adapter)
        return self._run_inner(dev_mode=False)

    def run_without_integrity(self) -> RunOutcome:
        """DEVELOPMENT ONLY: skip integrity gates so the wiring can be tested
        before Phase C (validity metrics) and Phase D (seen/unseen tagging).

        Produced reports are stamped ``dev_mode=True`` (via run_id prefix)
        so they cannot be silently promoted to publication-ready artifacts.
        """
        print(
            "  ⚠ run_without_integrity() — INTEGRITY GATES SKIPPED — "
            "results are NOT publication-grade"
        )
        return self._run_inner(dev_mode=True)

    # ----------------------------------------------------------------------- #
    # Internals                                                               #
    # ----------------------------------------------------------------------- #

    def _run_inner(self, *, dev_mode: bool) -> RunOutcome:
        # Refuse unsupported modes upfront
        if "llm_alone" in self.config.modes:
            raise NotImplementedError(
                "llm_alone mode is Phase B of the task scope — see "
                "opensre-benchmark-task-scope.md. Run with modes=['opensre+llm'] only."
            )

        run_id = self._build_run_id(dev_mode=dev_mode)
        output_dir = self.config.output_dir / run_id
        cases_dir = output_dir / "cases"
        cases_dir.mkdir(parents=True, exist_ok=True)

        started_at = datetime.now(UTC).isoformat()
        cells: list[_CellResult] = []
        aborted = False
        abort_reason: str | None = None

        cases = list(
            self.adapter.load_cases(
                CaseFilters(
                    systems=self.config.filters.systems,
                    fault_categories=self.config.filters.fault_categories,
                    difficulty=self.config.filters.difficulty,
                    seen_shape=self.config.filters.seen_shape,
                    case_ids=self.config.filters.case_ids,
                    limit=self.config.filters.limit,
                    seed=self.config.seed,
                )
            )
        )
        print(f"  loaded {len(cases)} case(s)")

        try:
            for case in cases:
                for mode in self.config.modes:
                    mode_cast: Mode = cast(Mode, mode)
                    for llm in self.config.llms:
                        for run_index in range(self.config.runs_per_case):
                            cell = self._run_one_cell(
                                case=case,
                                mode=mode_cast,
                                llm=llm,
                                run_index=run_index,
                                cases_dir=cases_dir,
                            )
                            cells.append(cell)
        except CostBudgetExceeded as exc:
            aborted = True
            abort_reason = str(exc)
            print(f"  ✗ aborted: {abort_reason}")

        ended_at = datetime.now(UTC).isoformat()

        # Build the report (per-stratum aggregation)
        per_stratum = _aggregate_per_stratum(cells, self.adapter.metric_schema().all_metrics())
        negative = _build_negative_results(cells, self.adapter)
        config_hash = _hash_config(self.config)

        report = make_baseline_report(
            run_id=run_id,
            config_hash=config_hash,
            started_at=started_at,
            ended_at=ended_at,
            per_stratum=per_stratum,
            reported_metrics=self.adapter.metric_schema().all_metrics(),
            raw_artifacts_dir=cases_dir,
            pre_registration_path=self.config.pre_registration_path or Path("dev-mode-no-prereg"),
            negative_results=negative or "(no losses or ties recorded in this run)",
        )

        # Persist a JSON sidecar to output_dir/report.json regardless of validation
        (output_dir / "report.json").write_text(
            json.dumps(_report_to_dict(report, self.cost), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # Production runs gate emission on report_validation; dev runs skip
        if not dev_mode:
            self.integrity.report_validation(report, self.adapter)

        return RunOutcome(report=report, cells=cells, aborted=aborted, abort_reason=abort_reason)

    def _run_one_cell(
        self,
        *,
        case: BenchmarkCase,
        mode: Mode,
        llm: str,
        run_index: int,
        cases_dir: Path,
    ) -> _CellResult:
        """Execute one (case × mode × llm × run) cell."""
        # Late import — keeps the rest of the framework importable without
        # opensre's full dep tree loaded.
        from app.pipeline.runners import run_investigation

        alert = self.adapter.build_alert(case)
        integrations = self.adapter.build_opensre_integrations(case)
        started = datetime.now(UTC)
        t0 = time.monotonic()
        ok = True
        error: str | None = None
        final_state_dict: dict[str, Any] = {}

        try:
            final_state = run_investigation(alert.raw, resolved_integrations=integrations)
            final_state_dict = dict(final_state)
        except Exception as exc:
            ok = False
            error = f"{type(exc).__name__}: {exc}"

        latency_ms = int((time.monotonic() - t0) * 1000)
        ended = datetime.now(UTC)

        # Cost tracking is a no-op until llm_dispatch wires token counts in.
        # When that ships, this is where ``self.cost.add(model, tin, tout)``
        # will run and may raise CostBudgetExceeded.

        run = RunResult(
            case_id=case.case_id,
            mode=mode,
            llm=llm,
            model_version=self.config.model_versions.get(llm, "(unpinned)"),
            opensre_sha=self._opensre_sha,
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            ok=ok,
            error=error,
            final_diagnosis={
                "stage": final_state_dict.get("root_cause_category") or "",
                "component": "",
                "root_cause": final_state_dict.get("root_cause") or "",
                "report": final_state_dict.get("report") or "",
            },
            evidence_entries=list(cast(list[Any], final_state_dict.get("evidence_entries") or [])),
            tokens_in=0,  # llm_dispatch fills this
            tokens_out=0,
            cost_usd=0.0,
            latency_ms=latency_ms,
        )

        score = self.adapter.score_case(case, run)

        # Per-cell artifact
        artifact_path = (
            cases_dir / f"{case.case_id.replace('/', '_')}__{mode}__{llm}__{run_index}.json"
        )
        artifact_path.write_text(
            json.dumps(
                _cell_to_dict(case, run, score),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        print(
            f"  {case.case_id} [{mode} · {llm} · run {run_index}] "
            f"a1={score.metrics.get('a1', 0):.2f} "
            f"steps={score.metrics.get('steps', 0):.0f} "
            f"{latency_ms}ms"
        )

        return _CellResult(
            case=case,
            mode=mode,
            llm=llm,
            run_index=run_index,
            run=run,
            score=score,
            artifact_path=artifact_path,
        )

    # ----------------------------------------------------------------------- #
    # Helpers                                                                 #
    # ----------------------------------------------------------------------- #

    def _build_run_id(self, *, dev_mode: bool) -> str:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        prefix = "dev-" if dev_mode else ""
        return f"{prefix}{ts}_{self.adapter.name}"


# --------------------------------------------------------------------------- #
# Aggregation + serialization helpers                                          #
# --------------------------------------------------------------------------- #


def _aggregate_per_stratum(
    cells: list[_CellResult], metrics: list[str]
) -> dict[str, dict[str, dict[str, float]]]:
    """Aggregate cell metrics into the per_stratum shape IntegrityGuard expects.

    Shape: {stratum: {f"{mode}/{llm}": {metric: median_value}}}

    For v1, only the `all` stratum is populated. Phase D adds seen/unseen
    tagging which extends to multi-stratum.
    """
    by_stratum_mode_llm: dict[str, dict[str, dict[str, list[float]]]] = {"all": {}}

    for cell in cells:
        key = f"{cell.mode}/{cell.llm}"
        all_bucket = by_stratum_mode_llm["all"].setdefault(key, {m: [] for m in metrics})
        for m in metrics:
            all_bucket[m].append(cell.score.metrics.get(m, 0.0))
        # Per-stratum (seen-shape, unseen-shape, mid-shape) — only when tagged
        if cell.case.seen_shape is True:
            stratum_bucket = by_stratum_mode_llm.setdefault("seen-shape", {}).setdefault(
                key, {m: [] for m in metrics}
            )
            for m in metrics:
                stratum_bucket[m].append(cell.score.metrics.get(m, 0.0))
        elif cell.case.seen_shape is False:
            stratum_bucket = by_stratum_mode_llm.setdefault("unseen-shape", {}).setdefault(
                key, {m: [] for m in metrics}
            )
            for m in metrics:
                stratum_bucket[m].append(cell.score.metrics.get(m, 0.0))

    return {
        stratum: {
            mode_llm: {m: _median(values) for m, values in metric_bucket.items()}
            for mode_llm, metric_bucket in by_mode_llm.items()
        }
        for stratum, by_mode_llm in by_stratum_mode_llm.items()
    }


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _build_negative_results(cells: list[_CellResult], adapter: BenchmarkAdapter) -> str:
    """Build the negative-results section: cases where a1 == 0.

    Honest reporting per integrity Mechanism 9.
    """
    losses = [c for c in cells if c.score.metrics.get("a1", 0.0) == 0.0]
    if not losses:
        return ""
    lines = [
        f"opensre lost or tied on {len(losses)} of {len(cells)} cell(s) (adapter={adapter.name}):"
    ]
    for c in losses[:50]:  # cap output
        lines.append(
            f"  - {c.case.case_id}  mode={c.mode}  llm={c.llm}  run={c.run_index}  "
            f"a1=0.00  artifact={c.artifact_path.name}"
        )
    if len(losses) > 50:
        lines.append(f"  ... and {len(losses) - 50} more (see report.json for full list)")
    return "\n".join(lines)


def _hash_config(config: BenchmarkConfig) -> str:
    """Stable hash of the config so two runs of the same config can be diffed."""
    serialized = json.dumps(config.model_dump(mode="json"), sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def _git_sha() -> str:
    """opensre git SHA for the running code. Used in RunResult for reproducibility."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).parent,
        )
        sha = result.stdout.strip()
        if not sha:
            return "(unknown)"
        # Check for uncommitted changes
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).parent,
        )
        suffix = "-dirty" if dirty.stdout.strip() else ""
        return f"{sha}{suffix}"
    except (FileNotFoundError, OSError):
        return "(no-git)"


def _cell_to_dict(case: BenchmarkCase, run: RunResult, score: CaseScore) -> dict[str, Any]:
    """Serializable shape for per-case artifact JSON."""
    return {
        "case": {
            "case_id": case.case_id,
            "benchmark_name": case.benchmark_name,
            "metadata": case.metadata,
            "seen_shape": case.seen_shape,
        },
        "run": {
            "mode": run.mode,
            "llm": run.llm,
            "model_version": run.model_version,
            "opensre_sha": run.opensre_sha,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "ok": run.ok,
            "error": run.error,
            "final_diagnosis": run.final_diagnosis,
            "evidence_entries_count": len(run.evidence_entries),
            "tokens_in": run.tokens_in,
            "tokens_out": run.tokens_out,
            "cost_usd": run.cost_usd,
            "latency_ms": run.latency_ms,
        },
        "score": {
            "metrics": score.metrics,
            "failure_reason": score.failure_reason,
        },
    }


def _report_to_dict(report: BenchmarkReport, cost: CostTracker) -> dict[str, Any]:
    """Serializable shape for report.json."""
    return {
        "run_id": report.run_id,
        "config_hash": report.config_hash,
        "started_at": report.started_at,
        "ended_at": report.ended_at,
        "per_stratum": report.per_stratum,
        "reported_metrics": report.reported_metrics,
        "negative_results": report.negative_results,
        "coi_disclosure": report.coi_disclosure,
        "raw_artifacts_dir": str(report.raw_artifacts_dir) if report.raw_artifacts_dir else None,
        "pre_registration_path": str(report.pre_registration_path)
        if report.pre_registration_path
        else None,
        "cost": cost.summary(),
        "opensre_sha": _git_sha(),
        "host": {"user": os.environ.get("USER", ""), "cwd": str(Path.cwd())},
    }
