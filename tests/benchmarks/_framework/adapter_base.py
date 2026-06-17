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
    from app.agent.investigation import ConnectedInvestigationAgent


# --------------------------------------------------------------------------- #
# Capability flags                                                            #
# --------------------------------------------------------------------------- #


class AdapterCapabilities(BaseModel):
    """True / False flags the adapter sets so the framework knows what
    it supports.

    The framework used to check the adapter's name (``if benchmark ==
    "cloudopsbench"``) before allowing config fields like
    ``agent_variant``. That meant adding a new benchmark required
    editing framework code. Now the adapter declares which features it
    supports and the framework reads that declaration.

    Every flag defaults to ``False`` so a new adapter is safe by
    default: nothing is enabled until the adapter says it is. Adding a
    new feature in the future just means adding another field here.

    Adapters set their flags as a class attribute:

        class MyAdapter(BenchmarkAdapter):
            capabilities = AdapterCapabilities(
                supports_agent_variant=True,
            )
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    supports_agent_variant: bool = False
    """The adapter knows what to do with the ``agent_variant`` config
    field.

    When this is ``False`` and a config sets ``agent_variant`` to
    anything other than ``"default"``, the framework rejects the config
    instead of running the default agent quietly. CloudOpsBench uses
    this flag to enable its trimmed-prompt agent variant. Adapters that
    only have one kind of agent leave this off.
    """

    supports_predictor_variant: bool = False
    """The adapter has a predictor step and reads ``predictor_variant``.

    When this is ``False``, a config setting ``predictor_variant`` to
    anything other than ``"default"`` is rejected. CloudOpsBench has a
    predictor step (it produces the paper's three-part answer format).
    Other benchmark types (pure investigation, tool-call) do not, so
    they leave this off.
    """


# --------------------------------------------------------------------------- #
# Overfit-dimensions schema                                                   #
# --------------------------------------------------------------------------- #


class OverfitDimensions(BaseModel):
    """Key names the overfit guards use to read fields from a case.

    The overfit guards check whether opensre's wins are spread across
    the corpus or piled up in one place. To do that they need to read
    three fields off each case: which system the case came from, which
    category of fault it is, and what the ground-truth target object is.

    Different benchmarks store these under different key names. The
    defaults below match how CloudOpsBench stores them. A new benchmark
    that stores them differently overrides this model to point at its
    own keys.

    Before this hook existed, the guard code looked at hard-coded keys
    like ``metadata["system"]`` directly. That made the framework only
    work for CloudOpsBench-shaped data.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    system_key: str = "system"
    """Which key in ``case.metadata`` holds the system name."""

    stratum_key: str = "fault_category"
    """Which key in ``case.metadata`` holds the category / stratum."""

    gt_object_key: str = "fault_object"
    """Which key inside ``case.metadata["ground_truth"]`` holds the
    target object name. Used by the cluster-concentration guard to
    group similar cases together."""


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
        """Optional hook for the adapter to read its own config fields.

        Called once after the framework builds the adapter and before
        any agent runs. Use this when the config has fields that only
        your adapter understands (CloudOpsBench uses this for
        ``min_tool_calls`` and ``agent_variant``). Each adapter handles
        its own settings here so the framework does not need to know
        about them.

        Default does nothing. If your adapter has no extra settings,
        leave this alone.
        """
        return None

    def overfit_dimensions(self) -> OverfitDimensions:
        """Tell the overfit guards which metadata keys hold "system",
        "category", and "ground-truth object" for this adapter.

        The default returns the CloudOpsBench layout. Override this if
        your benchmark stores those values under different key names.

        Before this hook existed, the guard code reached into hard-coded
        keys like ``metadata["system"]``. That broke as soon as a second
        benchmark used different key names.
        """
        return OverfitDimensions()

    def extend_provenance(self, provenance: dict[str, Any]) -> dict[str, Any]:
        """Optional hook to add adapter-specific entries to provenance.

        The framework builds a standard provenance bundle (code SHA,
        config, model versions, environment, etc.) then calls this hook
        to let the adapter add or change anything that is specific to
        its benchmark. Adapters can:

          - add a brand-new top-level key, or
          - add a key inside an existing section, or
          - return the dict unchanged.

        The default does nothing. The point of the hook is to keep the
        framework's provenance code free of adapter-specific imports.
        For example, CloudOpsBench uses this hook to add the
        ``min_tool_calls`` value into ``run_inputs``. Before the hook
        existed, the framework imported from CloudOpsBench directly to
        get that value, which broke the decoupling.

        Implementations can either mutate ``provenance`` in place and
        return it, or return a fresh dict. The framework respects
        whatever the hook returns.
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
