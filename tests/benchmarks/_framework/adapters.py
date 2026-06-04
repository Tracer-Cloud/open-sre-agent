"""Abstract benchmark adapter + typed data contracts.

The framework calls into adapters via ``BenchmarkAdapter``. Each benchmark
suite (CloudOpsBench, OpenRCA, ToolCallBench) implements this interface;
the framework handles parallelism, LLM dispatch, output, cost tracking,
integrity guards.

This module deliberately has zero ``app.*`` imports — the framework is
independent of opensre internals. Adapters bridge to opensre.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    # Type-only import — preserves the framework's "zero ``app.*`` imports"
    # constraint at runtime while still letting type-checkers validate
    # that adapter overrides return an investigation-agent subclass.
    from app.agent.investigation import ConnectedInvestigationAgent

# --------------------------------------------------------------------------- #
# Mode — opensre+LLM vs LLM-alone. Framework-level concept; same adapter +    #
# same case work for both modes.                                              #
# --------------------------------------------------------------------------- #

Mode = Literal["opensre+llm", "llm_alone"]


# --------------------------------------------------------------------------- #
# Case selection                                                              #
# --------------------------------------------------------------------------- #


class CaseFilters(BaseModel):
    """User-supplied filter for which cases to load.

    Filters are AND-combined. Empty list/None means "no filter on this dim".
    """

    systems: list[str] = Field(default_factory=list)
    fault_categories: list[str] = Field(default_factory=list)
    difficulty: list[Literal["easy", "medium", "hard"]] = Field(default_factory=list)
    # opensre-specific tag; populated post-tagging (Phase D)
    seen_shape: list[bool] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    limit: int | None = None
    # Seeded random sample for fair selection — required by integrity Mechanism 6
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


# --------------------------------------------------------------------------- #
# The adapter interface                                                       #
# --------------------------------------------------------------------------- #


class BenchmarkAdapter(ABC):
    """One adapter per benchmark suite.

    Implementations:
      - ``tests/benchmarks/cloudopsbench/adapter.py``  (first)
      - ``tests/benchmarks/openrca_scenarios/adapter.py``  (proves reusability)
      - ``tests/benchmarks/toolcall_model_benchmark/adapter.py``  (proves reusability)

    The framework calls these methods; adapters bridge to whatever the
    specific benchmark needs (HF datasets, replay backends, custom scoring).
    """

    name: str  # e.g. "cloudopsbench"
    version: str  # adapter version, separate from corpus version

    @abstractmethod
    def load_cases(self, filters: CaseFilters) -> Iterator[BenchmarkCase]:
        """Stream cases matching the filter. Seeded random selection is the
        adapter's responsibility (integrity Mechanism 6).
        """

    @abstractmethod
    def build_alert(self, case: BenchmarkCase) -> AlertPayload:
        """Convert a case into the alert opensre / LLM consume."""

    @abstractmethod
    def build_opensre_integrations(self, case: BenchmarkCase) -> dict[str, Any]:
        """Return the resolved_integrations dict opensre+LLM mode passes to
        ``run_investigation``. For CloudOpsBench, this wires the replay
        backend in place of live AWS/K8s/Datadog clients.
        """

    @abstractmethod
    def build_baseline_tools(self, case: BenchmarkCase) -> dict[str, Any]:
        """Return the tool surface for LLM-alone mode. Same replay backend
        access as opensre+LLM (fairness) but no extract/context/diagnose
        pipeline — just direct LLM with tool-calling.
        """

    @abstractmethod
    def score_case(self, case: BenchmarkCase, run: RunResult, context: RunContext) -> CaseScore:
        """Compute per-case metrics from the run result + per-cell context.

        ``context.integrations`` is the dict ``build_opensre_integrations``
        returned for THIS cell — adapters use it to read runtime state
        accumulated during the run (e.g., a replay backend's action_log).

        Passing context explicitly (vs caching on the adapter) is what
        makes the adapter thread-safe for parallel runner execution.
        """

    @abstractmethod
    def metric_schema(self) -> MetricSchema:
        """Declare which metrics this adapter emits, for CLI validation +
        comparable reporting across adapters.
        """

    def investigation_agent_class(self) -> type[ConnectedInvestigationAgent] | None:
        """Optional: which investigation agent class should the runner use?

        Default ``None`` — let the production pipeline construct its standard
        :class:`ConnectedInvestigationAgent`. Override when the benchmark
        needs a stricter termination policy or other agent-level behavior
        (e.g. CloudOpsBench's minimum-tool-call floor lives in
        :class:`tests.benchmarks.cloudopsbench.bench_agent.BenchInvestigationAgent`).

        Production code stays clean: the runner just passes whatever the
        adapter returns to ``run_investigation``. Bench-specific agent logic
        lives entirely in bench code.
        """
        return None

    def format_final_answer(
        self,
        case: BenchmarkCase,  # noqa: ARG002 — used by overrides
        run: RunResult,
        spec: Any,  # noqa: ARG002 — used by overrides
    ) -> RunResult:
        """Optional: enrich ``run.final_diagnosis`` before ``score_case``.

        Default no-op — returns the run unchanged. Override when the
        benchmark's scorer expects a specific output schema the
        investigation pipeline doesn't natively produce (e.g.,
        CloudOpsBench requires paper-format ``top_3_predictions`` JSON
        and runs a separate LLM call to emit it).

        ``spec`` is the framework's LLMSpec for this cell — typed as
        ``Any`` here to keep ``adapters.py`` free of llm_dispatch import
        coupling; the override casts it to its real type.

        Mode-agnostic by design: the runner calls this for every cell
        regardless of mode, so the same hook serves both ``opensre+llm``
        (with investigation evidence) and future ``llm_alone`` (without).
        """
        return run
