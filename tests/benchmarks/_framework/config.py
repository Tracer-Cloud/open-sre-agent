"""YAML config loader + integrity-aware validation.

The benchmark framework is YAML-driven (Yauhen's stated requirement: easy
to configure, parallel by default). Configs live under
``tests/benchmarks/cloudopsbench/configs/*.yml``. Loading a config goes through these
validation layers:

  1. Pydantic — types and field constraints (always-on, fast).
  2. ``BenchmarkConfig.lint()`` — anti-pattern checks (Gregg Ch 2 § 2.5
     applied to benchmark configs): no replication, missing model pins,
     missing cost budget, etc.

The framework refuses to start a run on a config that fails layer 2.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from tests.benchmarks._framework.adapters import Mode


def _default_report_formats() -> list[Literal["json", "markdown", "html"]]:
    """Module-level factory keeps mypy happy with the Literal element type."""
    return ["json", "markdown"]


# --------------------------------------------------------------------------- #
# Config schema                                                               #
# --------------------------------------------------------------------------- #


class FiltersConfig(BaseModel):
    """User-supplied case filters. Empty list = no filter on that dim."""

    systems: list[str] = Field(default_factory=list)
    fault_categories: list[str] = Field(default_factory=list)
    difficulty: list[Literal["easy", "medium", "hard"]] = Field(default_factory=list)
    seen_shape: list[bool] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    limit: int | None = None


class BenchmarkConfig(BaseModel):
    """Top-level config for one benchmark run.

    Example minimal YAML::

        benchmark: cloudopsbench
        modes: [opensre+llm]
        llms: [claude-4-sonnet]
        model_versions:
          claude-4-sonnet: claude-sonnet-4-5-20250929
        runs_per_case: 3
        workers: 4
        cost_budget_usd: 100
        seed: 42
        output_dir: .bench-results/test-run/
    """

    # Which adapter to use
    benchmark: str

    # Which modes to run (typically both for paired comparison)
    modes: list[Mode] = Field(min_length=1)

    # LLMs to test (one row per LLM × mode × case × run)
    llms: list[str] = Field(min_length=1)

    # Locked model versions — refused at runtime if a model resolves
    # to a different snapshot (integrity Mechanism: standardization)
    model_versions: dict[str, str]

    # Replication count per cell — required for honest variance estimate
    # (Box-Hunter-Hunter Ch 3.4)
    runs_per_case: int = Field(ge=1, default=3)

    # Parallel workers — default sized to laptop; bump on AWS
    workers: int = Field(ge=1, default=4)

    # Hard cap on API spend — framework aborts cleanly when exceeded
    # (Principle 11: cost as first-class metric)
    cost_budget_usd: float = Field(gt=0)

    # Seeded random case selection — Mechanism 6 (no cherry-picking)
    seed: int

    # Where artifacts land
    output_dir: Path

    # Optional case filtering
    filters: FiltersConfig = Field(default_factory=FiltersConfig)

    # Required for integrity Phase 0: pre-registration path
    # If unset, framework refuses to start the run.
    pre_registration_path: Path | None = None

    # Required formats — at least one
    report_formats: list[Literal["json", "markdown", "html"]] = Field(
        default_factory=_default_report_formats, min_length=1
    )

    # Adapter-specific termination floor (currently honored only by the
    # CloudOpsBench adapter's ``BenchInvestigationAgent``). When set, the
    # CLI overrides ``BenchInvestigationAgent.MIN_TOOL_CALLS`` to this
    # value before the run starts — keeping the floor as part of the
    # experiment definition rather than a launch-time env var. Leave
    # ``None`` to inherit the agent's default (which itself can be
    # overridden by the ``BENCH_MIN_TOOL_CALLS`` env var at import time).
    # Required for floor-ablation experiments so the floor is reproducible
    # from the config file alone — see ``cloudopsbench_floor_ablation_v2_openai.yml``.
    min_tool_calls: int | None = Field(ge=0, default=None)

    # ----------------------------------------------------------------------- #
    # Pydantic-level validation                                               #
    # ----------------------------------------------------------------------- #

    @model_validator(mode="after")
    def _model_versions_cover_all_llms(self) -> BenchmarkConfig:
        """Every LLM in ``llms`` must have a pinned version."""
        missing = set(self.llms) - set(self.model_versions.keys())
        if missing:
            raise ValueError(
                f"model_versions missing pinned snapshot for: {sorted(missing)}. "
                f"Pin every LLM in ``llms`` for reproducibility "
                f"(integrity Mechanism: standardization)."
            )
        return self

    # ----------------------------------------------------------------------- #
    # Anti-pattern lint — refuses configs that would produce dishonest results #
    # (Principle 8 + Gregg Ch 2 § 2.5 applied to benchmark configs)            #
    # ----------------------------------------------------------------------- #

    def lint(self) -> list[str]:
        """Return list of anti-pattern errors. Empty list = config is honest.

        Refuses configs exhibiting these anti-patterns:

        - **Streetlight**: no validity metric declared by chosen adapter
          (caught later by adapter MetricSchema)
        - **Premature Conclusion**: ``runs_per_case < 3``
          (single-run is statistical foot-gun for stochastic LLMs)
        - **No Variance Reporting**: framework default reports median+IQR;
          configurable here in future
        - **Ad Hoc Checklist**: missing pre_registration_path
          (Phase 0 integrity gate)
        - **Marketing Narrative**: no negative_results requirement
          (framework default — flagged here for awareness)
        - **Random Change** signals: too many LLMs × modes × cases for one
          cycle (recommend breaking into sub-runs)
        """
        errors: list[str] = []

        if self.runs_per_case < 3:
            errors.append(
                f"runs_per_case={self.runs_per_case} < 3 — single runs of "
                "stochastic LLMs are unreliable. Set runs_per_case >= 3 "
                "(Box-Hunter-Hunter Ch 3.4)."
            )

        if self.pre_registration_path is None:
            errors.append(
                "pre_registration_path is unset — integrity Phase 0 requires "
                "expected_deltas committed to disk BEFORE the run starts. "
                "Set pre_registration_path to a .yml file committed to git."
            )

        # Crude size check — warns rather than blocks
        # Estimate: 452 (cloudopsbench full) × len(llms) × len(modes) × runs_per_case
        estimated_runs = 452 * len(self.llms) * len(self.modes) * self.runs_per_case
        if estimated_runs > 20000:
            errors.append(
                f"Estimated {estimated_runs} runs in one cycle — too large "
                "for variance attribution. Split into multiple sub-runs."
            )

        if self.cost_budget_usd > 10_000:
            errors.append(
                f"cost_budget_usd=${self.cost_budget_usd:,.0f} is unusually "
                "large for a single run. Confirm intent in pre-registration."
            )

        # Output dir must not be a managed system path. Compare BOTH the lexical
        # form and the resolved form (on macOS /etc → /private/etc symlink would
        # bypass a check against only one). The narrow prefix list intentionally
        # excludes user-writable temp paths like /var/folders (pytest tmpdir) and
        # /var/tmp.
        lexical = str(self.output_dir)
        resolved = str(self.output_dir.resolve()) if self.output_dir.is_absolute() else lexical
        system_prefixes = (
            "/etc/",
            "/usr/",
            "/var/log/",
            "/var/lib/",
            "/var/run/",
            "/private/etc/",
            "/private/var/log/",
            "/private/var/lib/",
            "/private/var/run/",
        )
        system_exacts = {"/", "/etc", "/usr", "/var", "/private/etc", "/private/var"}
        if any(s in system_exacts or s.startswith(system_prefixes) for s in (lexical, resolved)):
            errors.append(f"output_dir={self.output_dir} would write to a system path — refuse.")

        return errors


# --------------------------------------------------------------------------- #
# Loader                                                                      #
# --------------------------------------------------------------------------- #


def load_config(path: Path) -> BenchmarkConfig:
    """Read YAML, parse via Pydantic, leave linting to caller.

    The two-step (parse → lint) lets callers decide whether to abort or
    warn on lint failures. The framework's runner refuses to start on
    any lint failure.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ValueError(f"Config file {path} must be a YAML mapping; got {type(raw).__name__}")

    # Honor a few env-var overrides used in CI (override workers + budget
    # without editing the file)
    if env_workers := os.environ.get("OPENSRE_BENCH_WORKERS"):
        raw["workers"] = int(env_workers)
    if env_budget := os.environ.get("OPENSRE_BENCH_COST_BUDGET_USD"):
        raw["cost_budget_usd"] = float(env_budget)

    return BenchmarkConfig.model_validate(raw)


def validate_config_or_raise(path: Path) -> BenchmarkConfig:
    """Load + lint + raise on either failure. Use this from the runner's
    pre-flight stage; use ``load_config`` + manual ``.lint()`` from tooling
    that wants to inspect errors without raising.
    """
    config = load_config(path)
    errors = config.lint()
    if errors:
        raise ValueError(
            "Benchmark config failed integrity lint:\n" + "\n".join(f"  - {e}" for e in errors)
        )
    return config
