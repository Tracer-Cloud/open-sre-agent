"""Typed data contracts shared by the framework and its adapters.

Held in their own module (separate from the ``BenchmarkAdapter`` ABC and
the adapter registry) so adapters can import the types without pulling in
the ABC's late-binding circular-import-prone surface, and so the framework's
runner / cost / integrity layers don't have to import an unrelated adapter
interface to see a ``RunResult``.

Module organization (split from the original monolithic ``adapters.py``):
  - ``types.py`` (this file) — pure data contracts (Pydantic models +
    frozen dataclasses).
  - ``adapter_base.py`` — the ``BenchmarkAdapter`` abstract base class +
    its strategy-pattern ``apply_config_overrides`` hook.
  - ``registry.py`` — the global ``register_adapter`` / ``build_adapter``
    registry that lets the CLI dispatch by ``config.benchmark`` name.

``adapters.py`` re-exports everything from those three modules so
existing ``from tests.benchmarks._framework.adapters import ...`` callers
keep working.

This module deliberately has zero ``app.*`` imports — the framework is
independent of opensre internals. Adapters bridge to opensre.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Mode — opensre+LLM vs LLM-alone. Framework-level concept; same adapter +    #
# same case work for both modes.                                              #
# --------------------------------------------------------------------------- #

Mode = Literal["opensre+llm", "llm_alone", "llm_alone_pure"]


# --------------------------------------------------------------------------- #
# Case selection                                                              #
# --------------------------------------------------------------------------- #


class CaseFilters(BaseModel):
    """User-supplied case filters. Empty list = no filter on that dim.

    ``seed`` is required by integrity Mechanism 6 (no cherry-picking) — the
    adapter uses it to seed the random selection so case sub-samples are
    reproducible across runs. Dropping this field would silently break
    reproducibility: Pydantic v2 ignores unknown constructor kwargs by
    default, so ``CaseFilters(seed=42)`` would seem to succeed and then
    ``filters.seed`` would AttributeError downstream.
    """

    systems: list[str] = Field(default_factory=list)
    fault_categories: list[str] = Field(default_factory=list)
    difficulty: list[Literal["easy", "medium", "hard"]] = Field(default_factory=list)
    seen_shape: list[bool] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    limit: int | None = None
    seed: int | None = None


class BenchmarkCase(BaseModel):
    """One scenario the adapter loaded. Framework-agnostic shape.

    Per-adapter specifics live in ``metadata``. The framework reads only
    ``case_id``, ``benchmark_name``, and ``seen_shape``; everything else is
    adapter-private.
    """

    case_id: str
    benchmark_name: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    # opensre-specific tag; None until Phase D tagging is applied
    seen_shape: bool | None = None


# --------------------------------------------------------------------------- #
# Alert / integration payloads — what the adapter hands the runner            #
# --------------------------------------------------------------------------- #


class AlertPayload(BaseModel):
    """Shape an adapter produces to seed an investigation.

    ``raw`` is the verbatim alert (e.g., a Datadog webhook); ``normalized``
    is the extracted, agent-friendly form used by both opensre+LLM and
    LLM-alone modes.
    """

    raw: dict[str, Any]
    normalized: dict[str, Any]


# --------------------------------------------------------------------------- #
# Run result — what the runner produces per case-run                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunResult:
    """One complete case-run: opensre+LLM or LLM-alone, one LLM, one trial.

    Captured at framework level so any adapter's scorer can compute trace-
    based metrics. Per-row fields support:
      - reproducibility (model_version, opensre_sha, seed)
      - cost accounting (tokens, USD)
      - process scoring (evidence_entries trajectory)
      - paired comparison (case_id + mode + llm join key)
    """

    case_id: str
    mode: Mode
    llm: str
    model_version: str
    # opensre git SHA — pinned per result row (Principle: standardization)
    opensre_sha: str
    started_at: str  # ISO-8601 UTC
    ended_at: str
    ok: bool
    error: str | None
    # Diagnosis: {stage, component, root_cause}
    final_diagnosis: dict[str, Any]
    # Per-tool-call trace; same shape as opensre's AgentState.evidence_entries
    evidence_entries: list[dict[str, Any]]
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int


# --------------------------------------------------------------------------- #
# Scoring                                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CaseScore:
    """Per-case scoring output from an adapter.

    ``metrics`` keys are adapter-defined (see ``MetricSchema``). The
    framework treats values as floats and aggregates them across cells.
    """

    case_id: str
    metrics: dict[str, float]
    failure_reason: str | None = None


@dataclass(frozen=True)
class RunContext:
    """Per-cell context handed to ``score_case``.

    Lets the adapter access cell-local state (the integrations dict it
    built earlier — which carries adapter-specific runtime objects like
    the CloudOpsBench replay backend) WITHOUT keeping per-cell state on
    the adapter instance. Required for thread-safe parallel execution.
    """

    integrations: dict[str, Any]


class MetricSchema(BaseModel):
    """Adapter's metric inventory. Declared once per adapter.

    The framework uses ``higher_is_better`` to render comparison tables
    correctly (e.g., for IAC, lower is better). It also uses the family
    grouping to enforce multi-metric reporting per integrity Mechanism 3:
    at least one metric from each of ``outcome_metrics``, ``process_metrics``,
    and ``validity_metrics`` must be reported.
    """

    # Required: at least one outcome metric (per integrity Mechanism 3)
    outcome_metrics: list[str] = Field(min_length=1)
    process_metrics: list[str] = Field(default_factory=list)
    robustness_metrics: list[str] = Field(default_factory=list)
    validity_metrics: list[str] = Field(default_factory=list)
    efficiency_metrics: list[str] = Field(default_factory=list)
    # All metrics that appear above must have an entry here.
    higher_is_better: dict[str, bool]

    def all_metrics(self) -> list[str]:
        """Flat list of every metric name this adapter emits."""
        return (
            self.outcome_metrics
            + self.process_metrics
            + self.robustness_metrics
            + self.validity_metrics
            + self.efficiency_metrics
        )

    def validate_completeness(self) -> list[str]:
        """Return list of integrity errors. Empty means schema is honest.

        Enforces:
          - every metric listed has a direction in ``higher_is_better``
          - no orphan keys in ``higher_is_better`` (extra metrics)
          - at least one validity metric (Mechanism 9: process scoring)
        """
        errors: list[str] = []
        declared = set(self.all_metrics())
        directed = set(self.higher_is_better.keys())
        missing = declared - directed
        orphan = directed - declared
        if missing:
            errors.append(f"metrics missing direction in higher_is_better: {sorted(missing)}")
        if orphan:
            errors.append(f"higher_is_better has unknown metrics: {sorted(orphan)}")
        if not self.validity_metrics:
            errors.append(
                "no validity_metrics declared — integrity Mechanism 9 "
                "requires at least one validity metric "
                "(e.g., citation_grounding_rate, entity_existence_rate)"
            )
        return errors
