"""Abstract benchmark adapter base class.

Each benchmark suite (CloudOpsBench, OpenRCA, ToolCallBench) implements
this interface to bridge its corpus / scoring / agent surface to the
framework. The framework calls these methods; adapters do the
benchmark-specific work.

Split out from the original ``adapters.py`` so the type contracts in
``types.py`` and the registry in ``registry.py`` can be imported without
pulling in the late-binding TYPE_CHECKING surface this module needs to
type-check ``investigation_agent_class()``-style hooks against
``ConnectedInvestigationAgent``.

This module deliberately has zero ``app.*`` imports at module load — the
framework is independent of opensre internals. The TYPE_CHECKING block
below is type-checker-only and never executes at runtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict

from tests.benchmarks._framework.types import (
    AlertPayload,
    BenchmarkCase,
    CaseFilters,
    CaseScore,
    MetricSchema,
    RunContext,
    RunResult,
)

if TYPE_CHECKING:
    # Type-only import — preserves the framework's "zero ``app.*`` imports"
    # constraint at runtime while still letting type-checkers validate
    # that adapter overrides return an investigation-agent subclass.
    from core.orchestration.node.investigate import ConnectedInvestigationAgent


# --------------------------------------------------------------------------- #
# Capability flags                                                            #
# --------------------------------------------------------------------------- #


class AdapterCapabilities(BaseModel):
    """Feature flags an adapter declares to the framework.

    The framework uses these to validate config knobs without
    dispatching on adapter name. Every flag defaults to ``False``: a
    new adapter is locked down to the minimum surface until it opts in.

    Declare as a class attribute:

        class MyAdapter(BenchmarkAdapter):
            capabilities = AdapterCapabilities(supports_agent_variant=True)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    supports_agent_variant: bool = False
    """Adapter honors ``config.agent_variant``. If False, the framework
    rejects any config with ``agent_variant != "default"`` instead of
    silently running the default agent."""

    supports_predictor_variant: bool = False
    """Adapter has a predictor stage and honors
    ``config.predictor_variant``. If False, any non-default value is
    rejected. CloudOpsBench has one (paper-format triple emission);
    most other benchmark types don't."""


# --------------------------------------------------------------------------- #
# Overfit-dimensions schema                                                   #
# --------------------------------------------------------------------------- #


class OverfitDimensions(BaseModel):
    """Metadata key names the overfit guards read from each case.

    The guards group results by three axes — system, stratum, GT
    object — to detect concentration. Adapters override this if their
    cases store those values under different keys. Defaults match the
    CloudOpsBench schema.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    system_key: str = "system"
    """``case.metadata[<key>]`` — system / cluster name."""

    stratum_key: str = "fault_category"
    """``case.metadata[<key>]`` — category / stratum for per-stratum
    uniformity checks."""

    gt_object_key: str = "fault_object"
    """``case.metadata["ground_truth"][<key>]`` — GT target object,
    used by the cluster-concentration guard to fingerprint scenarios."""


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

    Adapters register themselves in the framework's ``adapter_registry`` so
    the CLI can dispatch on ``config.benchmark`` without an if/elif chain.
    See ``register_adapter`` / ``build_adapter`` / ``known_adapters`` in
    ``tests/benchmarks/_framework/registry.py``.
    """

    name: str  # e.g. "cloudopsbench"
    version: str  # adapter version, separate from corpus version
    capabilities: ClassVar[AdapterCapabilities] = AdapterCapabilities()
    """Framework features this adapter opts into.

    Default is the all-False instance: a new adapter is locked down to
    the minimum surface until it explicitly declares each capability.
    See :class:`AdapterCapabilities` for the available flags."""

    def apply_config_overrides(self, config: Any) -> None:  # noqa: ARG002 — default no-op
        """Read adapter-specific config fields before any agent runs.

        Called once by the CLI after the adapter is built. Use for
        config knobs only your adapter understands (CloudOpsBench reads
        ``min_tool_calls`` and ``agent_variant`` here). Default is
        no-op.
        """
        return None

    def overfit_dimensions(self) -> OverfitDimensions:
        """Metadata keys the overfit guards consult for this adapter.

        Override if your case metadata uses different key names than
        the CloudOpsBench defaults.
        """
        return OverfitDimensions()

    def extend_provenance(self, provenance: dict[str, Any]) -> dict[str, Any]:
        """Add adapter-specific entries to the provenance bundle.

        Called by ``capture_provenance`` after the framework assembles
        its standard sections (code, config, models, environment,
        run_inputs). Adapters may add top-level keys, extend existing
        sections, or return the dict unchanged.

        Default is identity. The hook exists so
        ``_framework/provenance.py`` does not need to import any
        specific adapter to capture adapter-specific run inputs (e.g.
        CloudOpsBench's ``min_tool_calls``).

        Mutate-and-return is fine; the framework uses whatever the
        hook returns.
        """
        return provenance

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

    def baseline_agent_class(self) -> type[ConnectedInvestigationAgent] | None:
        """Optional: which agent class to use for the ``llm_alone`` control arm.

        Default ``None`` — the adapter does not support an in-harness baseline,
        and the runner will refuse a config with ``modes=["llm_alone"]``.

        Override to return an agent class that represents the matched control
        for this benchmark's headline claim. The control's job is to isolate
        whichever lever you're attributing lift to — typically: same tool
        surface, same scoring, but no bench-specific termination policy.

        The runner picks this method for ``llm_alone`` cells and
        ``investigation_agent_class`` for ``opensre+llm`` cells, then passes
        the chosen class to ``run_investigation`` exactly the same way.
        """
        return None

    def pure_baseline_agent_class(self) -> type[ConnectedInvestigationAgent] | None:
        """Optional: agent class for the pure-baseline (``llm_alone_pure``) arm.

        Default ``None`` — the adapter does not ship a prompt-stripped
        baseline; runner refuses ``modes=["llm_alone_pure"]``.

        Override to return an agent that ALSO overrides ``_build_system_prompt``
        with a minimal task-specific prompt — no opensre planner / verifier /
        evidence-budget instructions. The contrast (opensre+llm) − (llm_alone_pure)
        then isolates the lift from opensre's full structural stack, not just
        the bench-specific termination policy that ``baseline_agent_class``
        controls.

        Same tool surface as both other arms; the methodological constant
        across all three modes is the per-case integrations dict.
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

    def select_best_run(
        self,
        case: BenchmarkCase,  # noqa: ARG002 — used by overrides
        runs: list[tuple[RunResult, CaseScore]],  # noqa: ARG002 — used by overrides
    ) -> int | None:
        """Optional: pick the canonical run from a self-consistency batch.

        Called once per (case, mode, llm) group after every run finishes.
        ``runs`` is the list of (RunResult, CaseScore) tuples in original
        run-index order.

        Return:
          - ``int`` — index of the run whose metrics should be reported as
            the canonical answer for this scenario. The runner emits an
            additional ``consistency_selected`` stratum built from those
            picks alongside the standard ``all`` (median) stratum.
          - ``None`` — no selection; only the median ``all`` stratum is
            reported. This is the default for adapters that don't run
            multi-seed self-consistency.

        Why this hook exists: paper-style A@1 averaging across N seeds
        drags the median below what the agent can actually produce. The
        06-05 CloudOpsBench run showed median a1=0.43 (gpt-4o) vs
        ORACLE bo3=0.83 — a 0.40 consistency gap. A free selector
        (majority vote on predicted root-cause taxonomy) closes 60% of
        that gap with zero extra LLM calls.

        The hook is opt-in per adapter so benchmarks without multi-seed
        protocols are unaffected. The runner still computes the standard
        median stratum so both views are reported side-by-side for
        transparency — no silent metric swap.
        """
        return None
