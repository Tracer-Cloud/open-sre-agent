"""Integrity guard — Pillar 0 of the benchmark framework.

Encodes the framework's honest-results discipline so that dishonest benchmark
runs and reports are structurally impossible to produce. See
``~/DevBox/opensre-notes/opensre-benchmark-framework.md`` § 0 for the full
mechanism catalogue.

This module provides two enforcement points:

  - ``IntegrityGuard.pre_flight(config, adapter)`` — runs BEFORE any case is
    executed. Refuses configs missing pre-registration, contamination-checked
    case selection, or required validity-metric coverage.

  - ``IntegrityGuard.report_validation(report)`` — runs BEFORE a report is
    emitted. Refuses reports missing per-stratum breakdown, negative-results
    section, or COI disclosure.

Both methods raise ``IntegrityViolation`` on any failure. The framework's
runner catches and halts the run; the reporting layer catches and refuses to
write the report. There is no way to bypass these guards short of editing the
framework itself — which is by design.

Mechanism 11 (judge calibration) is not enforced here because it depends on
the failure-analysis subsystem (BDIL Phase B); it is enforced separately by
``cycle.py`` when that ships.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tests.benchmarks._framework.adapters import BenchmarkAdapter
from tests.benchmarks._framework.config import BenchmarkConfig

# --------------------------------------------------------------------------- #
# Exceptions                                                                  #
# --------------------------------------------------------------------------- #


class IntegrityViolation(RuntimeError):
    """Raised when an integrity mechanism would be violated.

    Carries a list of violations so callers can show all of them at once
    rather than the engineer fixing one and re-running to discover the next.
    """

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        joined = "\n".join(f"  - {v}" for v in violations)
        super().__init__(f"Integrity violations:\n{joined}")


# --------------------------------------------------------------------------- #
# Report shape — what report_validation expects                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BenchmarkReport:
    """Minimum shape the reporting layer must produce.

    Each field is required (or explicitly default-empty); IntegrityGuard
    refuses reports that omit any of them.
    """

    run_id: str
    config_hash: str
    started_at: str
    ended_at: str
    # Per-stratum results — required (Mechanism 4: never aggregate-only)
    # Keys: stratum name (e.g., "seen-shape", "unseen-shape", "all")
    # Values: per-(mode, llm) metric aggregates
    per_stratum: dict[str, dict[str, dict[str, float]]]
    # Negative-results section — required (Mechanism 9)
    negative_results: str = ""
    # Conflict-of-interest disclosure — required (Mechanism 10)
    coi_disclosure: str = ""
    # All raw per-case artifact paths — required (Mechanism 5)
    raw_artifacts_dir: Path | None = None
    # Pre-registration source — required (Mechanism 1)
    pre_registration_path: Path | None = None
    # Per-metric reporting — must cover the adapter's full MetricSchema (Mechanism 3)
    reported_metrics: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# IntegrityGuard                                                              #
# --------------------------------------------------------------------------- #


class IntegrityGuard:
    """The framework's honest-results enforcement point.

    Two methods, two gates: pre-flight (before run) and report-validation
    (before report emission). Each raises ``IntegrityViolation`` listing
    every failed mechanism so the engineer fixes everything in one pass.
    """

    # ----------------------------------------------------------------------- #
    # Pre-flight (Phase 0 + integrity mechanisms enforced at config time)     #
    # ----------------------------------------------------------------------- #

    def pre_flight(self, config: BenchmarkConfig, adapter: BenchmarkAdapter) -> None:
        """Check config + adapter pass the pre-run integrity bar.

        Mechanisms enforced (numbering per framework doc § 0):
          1. Pre-registration committed before run
          3. Adapter declares ≥1 validity metric (no Streetlight)
          6. Seeded case selection (no cherry-picking)
          7. Data-contamination check has been considered (best-effort)
        """
        violations: list[str] = []

        # M1 — pre-registration path exists and points to a real, non-empty file
        if config.pre_registration_path is None:
            violations.append(
                "M1: pre_registration_path is unset. Integrity Phase 0 requires "
                "expected_deltas committed BEFORE the run starts. Set "
                "config.pre_registration_path and commit the file to git."
            )
        elif not config.pre_registration_path.exists():
            violations.append(
                f"M1: pre_registration_path={config.pre_registration_path} does not exist. "
                f"Pre-registration must be committed before the run."
            )
        elif config.pre_registration_path.stat().st_size == 0:
            violations.append(
                f"M1: pre_registration_path={config.pre_registration_path} is empty. "
                f"A real pre-registration with expected deltas is required."
            )

        # M3 — adapter declares enough metrics (multi-metric, no Streetlight)
        schema = adapter.metric_schema()
        schema_errors = schema.validate_completeness()
        for err in schema_errors:
            violations.append(f"M3: {err}")

        # M6 — seeded case selection (uniform random, not cherry-picked)
        if config.seed is None:
            violations.append(
                "M6: config.seed is None. Seeded random case selection is required "
                "so the case set is reproducible and not cherry-picked."
            )

        # M7 — contamination-check note. Best-effort: this is a warning unless
        # the adapter signals it has done contamination scoring. For v1 we
        # surface the obligation rather than enforce a specific check.
        contamination_checked = bool(getattr(adapter, "data_contamination_checked", False))
        if not contamination_checked:
            violations.append(
                f"M7: adapter '{adapter.name}' has not declared a data-contamination "
                f"check. Cloud-OpsBench released publicly Feb 2026; models trained "
                f"after may have seen the corpus. Set the adapter's "
                f"`data_contamination_checked = True` once a check has been run "
                f"(may be a documented review with no contamination flagged)."
            )

        if violations:
            raise IntegrityViolation(violations)

    # ----------------------------------------------------------------------- #
    # Report validation (mechanisms enforced at report-emission time)         #
    # ----------------------------------------------------------------------- #

    def report_validation(self, report: BenchmarkReport, adapter: BenchmarkAdapter) -> None:
        """Check a report meets the honest-output bar before emission.

        Mechanisms enforced (numbering per framework doc § 0):
          3. All adapter-declared metrics reported (no Streetlight)
          4. Per-stratum breakdown present (never aggregate-only)
          5. Raw per-case artifacts published
          9. Negative-results section present
         10. Conflict-of-interest disclosure present
        """
        violations: list[str] = []

        # M3 — all metrics declared by adapter must appear in the report
        declared = set(adapter.metric_schema().all_metrics())
        reported = set(report.reported_metrics)
        missing_metrics = declared - reported
        if missing_metrics:
            violations.append(
                f"M3: report omits adapter-declared metrics: {sorted(missing_metrics)}. "
                f"Selective metric reporting is a Marketing-Narrative anti-pattern; "
                f"report every metric the adapter emits, even when ugly."
            )

        # M4 — per-stratum present and not just "all"
        if not report.per_stratum:
            violations.append(
                "M4: report.per_stratum is empty. Per-stratum breakdown is required "
                "(no aggregate-only reporting)."
            )
        elif set(report.per_stratum.keys()) == {"all"}:
            violations.append(
                "M4: report.per_stratum only contains 'all' — per-stratum "
                "breakdown (e.g., seen-shape vs unseen-shape) is required to "
                "detect overfitting and to honor anti-overfit gates."
            )

        # M5 — raw artifacts published
        if report.raw_artifacts_dir is None:
            violations.append(
                "M5: report.raw_artifacts_dir is None. Raw per-case artifacts "
                "must be published so external parties can verify the result."
            )
        elif not report.raw_artifacts_dir.exists():
            violations.append(
                f"M5: report.raw_artifacts_dir={report.raw_artifacts_dir} does not "
                f"exist. Raw artifacts must be present on disk before the report "
                f"is emitted."
            )

        # M9 — negative results required
        if not report.negative_results.strip():
            violations.append(
                "M9: report.negative_results is empty. Reports must include a "
                "'where opensre lost or tied' section. If genuinely no losses, "
                "state that explicitly."
            )

        # M10 — COI disclosure
        if not report.coi_disclosure.strip():
            violations.append(
                "M10: report.coi_disclosure is empty. Conflict-of-interest "
                "disclosure is required — name who built opensre, who built "
                "the benchmark, who ran it, and who interpreted the results."
            )

        # M1 — pre-registration path must be carried into the report so
        # actuals can be diffed against expectations
        if report.pre_registration_path is None:
            violations.append(
                "M1: report.pre_registration_path is None. Report must "
                "carry forward the pre-registration so actuals-vs-expected "
                "is visible and auditable."
            )

        if violations:
            raise IntegrityViolation(violations)


# --------------------------------------------------------------------------- #
# Convenience: standard COI disclosure boilerplate                             #
# --------------------------------------------------------------------------- #


STANDARD_COI_DISCLOSURE: str = (
    "Conflict-of-interest disclosure: this benchmark run was authored, "
    "executed, and interpreted by the same person who builds opensre. "
    "Per the framework's integrity discipline, this structural bias is "
    "mitigated by (a) pre-registration committed before the run, "
    "(b) per-stratum reporting, (c) required negative-results section, "
    "(d) external replication of at least one cell before any public claim, "
    "(e) standardization-by-pinning of every parameter that affects results. "
    "Reviewers are encouraged to reproduce any cell independently."
)


def make_baseline_report(
    *,
    run_id: str,
    config_hash: str,
    started_at: str,
    ended_at: str,
    per_stratum: dict[str, dict[str, dict[str, float]]],
    reported_metrics: list[str],
    raw_artifacts_dir: Path,
    pre_registration_path: Path,
    negative_results: str,
    coi_disclosure: str | None = None,
) -> BenchmarkReport:
    """Construct a BenchmarkReport with the standard COI disclosure when
    not overridden. Convenience for the reporting layer; not a way to
    bypass IntegrityGuard.
    """
    return BenchmarkReport(
        run_id=run_id,
        config_hash=config_hash,
        started_at=started_at,
        ended_at=ended_at,
        per_stratum=per_stratum,
        reported_metrics=reported_metrics,
        raw_artifacts_dir=raw_artifacts_dir,
        pre_registration_path=pre_registration_path,
        negative_results=negative_results,
        coi_disclosure=coi_disclosure or STANDARD_COI_DISCLOSURE,
    )
