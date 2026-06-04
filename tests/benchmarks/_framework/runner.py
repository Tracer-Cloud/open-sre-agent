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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.utils.llm_retry import LLMCreditExhaustedError
from tests.benchmarks._framework.adapters import (
    BenchmarkAdapter,
    BenchmarkCase,
    CaseFilters,
    CaseScore,
    Mode,
    RunContext,
    RunResult,
)
from tests.benchmarks._framework.config import BenchmarkConfig
from tests.benchmarks._framework.cost import CostBudgetExceeded, CostTracker, UnknownModel
from tests.benchmarks._framework.integrity import (
    BenchmarkReport,
    IntegrityGuard,
    make_baseline_report,
)
from tests.benchmarks._framework.llm_dispatch import (
    LLMDispatcher,
    LLMSpec,
    MissingAPIKey,
    ModelVersionMismatch,
    UnknownLLM,
)
from tests.benchmarks._framework.provenance import capture_provenance
from tests.benchmarks._framework.reporting import render_report_dir

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
        dispatcher: LLMDispatcher | None = None,
        config_path: Path | None = None,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.integrity = integrity_guard or IntegrityGuard()
        self.cost = cost_tracker or CostTracker(budget_usd=config.cost_budget_usd)
        self.dispatcher = dispatcher or LLMDispatcher()
        self._opensre_sha = _git_sha()
        # Where the YAML was loaded from. Threaded into capture_provenance so
        # the run dir's provenance.json inlines the config content + sha256.
        # None when the runner is constructed inline (e.g. unit tests).
        self._config_path = config_path

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

        # Pre-flight: verify every LLM in config is registered AND that its
        # pinned model_version matches the spec. Fail-fast before any cell runs.
        # Raises UnknownLLM or ModelVersionMismatch; caller surfaces as failure.
        self._verify_llm_specs()

        run_id = self._build_run_id(dev_mode=dev_mode)
        output_dir = self.config.output_dir / run_id
        cases_dir = output_dir / "cases"
        cases_dir.mkdir(parents=True, exist_ok=True)

        started_at = datetime.now(UTC).isoformat()
        cells: list[_CellResult] = []
        aborted = False
        abort_reason: str | None = None

        # Capture provenance before any LLM call so reviewers can audit
        # exactly what code + config + env produced the report. Failure is
        # FATAL — a run without provenance has no reproducibility story.
        provenance = capture_provenance(
            config=self.config,
            adapter=self.adapter,
            run_id=run_id,
            started_at=started_at,
            config_path=self._config_path,
        )
        (output_dir / "provenance.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"  ✓ wrote {output_dir / 'provenance.json'}")

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

        # Register the cost-accounting hook so every successful LLM call
        # inside opensre's agent feeds CostTracker. Cleared in finally so
        # the hook doesn't leak into other test code that imports llm_client.
        from app.services.llm_client import set_usage_hook

        set_usage_hook(self.cost.add)

        # Serialize across LLMs (opensre's LLM client is a module-level
        # singleton — swapping mid-flight would race). Parallel within a
        # single LLM activation.
        try:
            for llm in self.config.llms:
                print(f"  ▶ activating LLM: {llm}")
                with self.dispatcher.activate(llm) as spec:
                    llm_cell_specs: list[tuple[BenchmarkCase, Mode, str, int]] = [
                        (case, cast(Mode, mode), llm, run_index)
                        for case in cases
                        for mode in self.config.modes
                        for run_index in range(self.config.runs_per_case)
                    ]
                    cells.extend(
                        self._execute_llm_batch(
                            specs=llm_cell_specs,
                            spec=spec,
                            cases_dir=cases_dir,
                        )
                    )
        except CostBudgetExceeded as exc:
            aborted = True
            abort_reason = str(exc)
            print(f"  ✗ aborted: {abort_reason}")
        except (UnknownLLM, ModelVersionMismatch, MissingAPIKey) as exc:
            aborted = True
            abort_reason = f"LLM dispatch failed: {exc}"
            print(f"  ✗ aborted: {abort_reason}")
        finally:
            set_usage_hook(None)

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

        # Auto-render markdown + HTML (or whichever formats the config requested).
        # Failure here is non-fatal — JSON is the source of truth; the
        # human-readable views can be regenerated via `bench report` later.
        render_formats = [f for f in self.config.report_formats if f != "json"]
        if render_formats:
            try:
                rendered = render_report_dir(output_dir, formats=render_formats)
                for fmt, path in rendered.items():
                    print(f"  ✓ rendered {fmt}: {path}")
            except Exception as exc:
                print(f"  ⚠ report rendering failed (JSON still written): {exc}")

        # Production runs gate emission on report_validation; dev runs skip
        if not dev_mode:
            self.integrity.report_validation(report, self.adapter)

        return RunOutcome(report=report, cells=cells, aborted=aborted, abort_reason=abort_reason)

    def _verify_llm_specs(self) -> None:
        """Pre-flight: confirm every LLM in config has a registered spec and
        the config's pinned ``model_versions[<llm>]`` matches.

        Raises UnknownLLM or ModelVersionMismatch from llm_dispatch — caught
        by _run_inner and surfaced as ``abort_reason``.
        """
        for llm in self.config.llms:
            self.dispatcher.spec(llm)  # raises UnknownLLM
            configured = self.config.model_versions.get(llm, "")
            self.dispatcher.verify_model_version(llm, configured)

    def _execute_llm_batch(
        self,
        *,
        specs: list[tuple[BenchmarkCase, Mode, str, int]],
        spec: LLMSpec,
        cases_dir: Path,
    ) -> list[_CellResult]:
        """Run a batch of cells under one already-activated LLM dispatcher.

        Within an LLM, parallel via ThreadPoolExecutor is safe (singleton
        is stable for the duration of the activation context).
        """
        results: list[_CellResult] = []
        if self.config.workers <= 1:
            for case, mode_cast, llm, run_index in specs:
                results.append(
                    self._run_one_cell(
                        case=case,
                        mode=mode_cast,
                        llm=llm,
                        spec=spec,
                        run_index=run_index,
                        cases_dir=cases_dir,
                    )
                )
            return results
        with ThreadPoolExecutor(max_workers=self.config.workers) as executor:
            future_to_spec = {
                executor.submit(
                    self._run_one_cell,
                    case=case,
                    mode=mode_cast,
                    llm=llm,
                    spec=spec,
                    run_index=run_index,
                    cases_dir=cases_dir,
                ): (case, mode_cast, llm, run_index)
                for case, mode_cast, llm, run_index in specs
            }
            for future in as_completed(future_to_spec):
                try:
                    results.append(future.result())
                except (CostBudgetExceeded, LLMCreditExhaustedError):
                    # Both are run-fatal: cost budget halts on operator-set
                    # cap; credit exhaustion halts because no retry can
                    # recover a dead provider account. Cancel pending
                    # futures so we don't burn time on cells destined to
                    # fail the same way.
                    for f in future_to_spec:
                        f.cancel()
                    raise
        return results

    def _run_one_cell(
        self,
        *,
        case: BenchmarkCase,
        mode: Mode,
        llm: str,
        spec: LLMSpec,
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
            final_state = run_investigation(
                alert.raw,
                resolved_integrations=integrations,
                agent_class=self.adapter.investigation_agent_class(),
            )
            final_state_dict = dict(final_state)
        except (CostBudgetExceeded, UnknownModel, LLMCreditExhaustedError):
            # Run-fatal: propagate up to _execute_llm_batch / _run_inner so
            # the run halts at the configured budget ceiling. Without this
            # explicit re-raise, the broad `except Exception` below would
            # silently record the breach as a per-cell failure and the run
            # would continue past the cap.
            #
            # UnknownModel: pre-flight problem (model missing from pricing
            # table) — must halt, not mask as cell failure.
            #
            # LLMCreditExhaustedError: provider billing/quota exhausted
            # (e.g. OpenAI insufficient_quota, Anthropic credit-balance-too-low).
            # Retries can't help — operator must top up balance. Run #2 of the
            # June-3 bench burned 1h42m wall-clock on this before the halt
            # path existed; halting on first occurrence prevents recurrence.
            raise
        except Exception as exc:
            ok = False
            error = f"{type(exc).__name__}: {exc}"

        latency_ms = int((time.monotonic() - t0) * 1000)
        ended = datetime.now(UTC)

        # Cost tracking happens out-of-band: app/services/llm_client._emit_usage
        # fires self.cost.add for every successful LLM call the agent makes,
        # so totals in report.json reflect real spend. Per-cell tokens/cost
        # below stay at 0 (delta capture is a follow-up — would need a
        # before/after snapshot bracketing run_investigation, complicated by
        # ThreadPoolExecutor shared-state).

        run = RunResult(
            case_id=case.case_id,
            mode=mode,
            llm=llm,
            # Pinned via llm_dispatch — what opensre's LLM client actually resolved to,
            # not what the user wrote in YAML (those must match by pre-flight check).
            model_version=spec.reasoning_model,
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

        # Adapter hook: optionally enrich run.final_diagnosis (e.g.,
        # CloudOpsBench emits paper-format top_3_predictions here so the
        # scorer doesn't have to inference from free-text RCA). Default
        # ABC implementation is a no-op for adapters that don't need it.
        run = self.adapter.format_final_answer(case, run, spec)

        score = self.adapter.score_case(case, run, RunContext(integrations=integrations))

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

    Strata populated:
      - ``all``                          — every cell
      - ``seen-shape`` / ``unseen-shape`` — Phase D tag from
        ``BenchmarkCase.seen_shape``; mid-shape cells appear only in ``all``
      - ``held-out`` / ``optimize``      — generalization-gate split from
        ``BenchmarkCase.metadata["is_held_out"]``; required by integrity
        Mechanism 8 so reports can compute ``held_out_lift / optimize_lift``
        per the pre-registration's ``generalization_gate`` clause
    """
    by_stratum_mode_llm: dict[str, dict[str, dict[str, list[float]]]] = {"all": {}}

    for cell in cells:
        key = f"{cell.mode}/{cell.llm}"

        def append_to(stratum: str, _cell: _CellResult = cell, _key: str = key) -> None:
            bucket = by_stratum_mode_llm.setdefault(stratum, {}).setdefault(
                _key, {m: [] for m in metrics}
            )
            for m in metrics:
                bucket[m].append(_cell.score.metrics.get(m, 0.0))

        append_to("all")
        if cell.case.seen_shape is True:
            append_to("seen-shape")
        elif cell.case.seen_shape is False:
            append_to("unseen-shape")

        held_out = cell.case.metadata.get("is_held_out") if cell.case.metadata else None
        if held_out is True:
            append_to("held-out")
        elif held_out is False:
            append_to("optimize")

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
