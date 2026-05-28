"""CloudOpsBench adapter — implements ``BenchmarkAdapter`` for the framework.

Wraps the existing CloudOpsBench machinery (HF dataset loader, State Snapshot
replay backend, 15-metric scorer) behind the framework's adapter interface
defined in ``tests/benchmarks/_framework/adapters.py``.

This module preserves the paper's protocol (Wang et al, arXiv:2603.00468v1)
by re-using the existing files unchanged:
  - ``case_loader.py`` — HF dataset access
  - ``replay_backend.py`` — State Snapshot via mocked tool interface
  - ``scoring.py`` — 15 paper metrics

The adapter adds:
  - Framework-compatible types (BenchmarkCase, AlertPayload, etc.)
  - Filter mapping (CaseFilters → case_loader's flat args)
  - Seeded random selection (integrity Mechanism 6)
  - Per-case backend lifecycle (build → run → score)

Validity metrics (citation_grounding, entity_existence, kubectl_actionability)
are NOT yet declared by this adapter — they ship in a follow-up commit (Phase C
of the task scope). The framework's IntegrityGuard will refuse to start a full
benchmark run until they are present.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tests.benchmarks._framework.adapters import (
    AlertPayload,
    BenchmarkAdapter,
    BenchmarkCase,
    CaseFilters,
    CaseScore,
    MetricSchema,
    RunContext,
    RunResult,
)
from tests.benchmarks.cloudopsbench.case_loader import (
    BENCHMARK_DIR,
    CloudOpsCase,
)
from tests.benchmarks.cloudopsbench.case_loader import (
    build_alert as _legacy_build_alert,
)
from tests.benchmarks.cloudopsbench.case_loader import (
    load_cases as _legacy_load_cases,
)
from tests.benchmarks.cloudopsbench.held_out_split import compute_held_out_set
from tests.benchmarks.cloudopsbench.replay_backend import CloudOpsBenchReplayBackend
from tests.benchmarks.cloudopsbench.scoring import score_case as _legacy_score_case
from tests.benchmarks.cloudopsbench.tags import seen_shape_for
from tests.benchmarks.cloudopsbench.validity_scoring import (
    compute_citation_grounding,
    compute_entity_existence,
    compute_kubectl_actionability,
)

# --------------------------------------------------------------------------- #
# Metric inventory — the paper's 15 metrics                                   #
# Validity metrics are added in a follow-up (Phase C).                        #
# --------------------------------------------------------------------------- #

_PAPER_METRIC_SCHEMA = MetricSchema(
    outcome_metrics=["a1", "a3", "partial_a1", "partial_a3", "tcr"],
    process_metrics=["exact", "in_order", "any_order", "rel", "cov"],
    efficiency_metrics=["steps", "mtti"],
    robustness_metrics=["iac", "rar", "ztdr"],
    # Phase C — heuristic validity metrics computed against the State Snapshot.
    # See validity_scoring.py for the heuristic limitations.
    validity_metrics=[
        "citation_grounding_rate",
        "entity_existence_rate",
        "kubectl_actionability_rate",
    ],
    higher_is_better={
        # Outcome (higher is better)
        "a1": True,
        "a3": True,
        "partial_a1": True,
        "partial_a3": True,
        "tcr": True,
        # Process — trajectory alignment + tool usage (higher better)
        "exact": True,
        "in_order": True,
        "any_order": True,
        "rel": True,
        "cov": True,
        # Efficiency (lower better — fewer steps, faster MTTI)
        "steps": False,
        "mtti": False,
        # Robustness (lower better — fewer invalid/redundant/zero-tool actions)
        "iac": False,
        "rar": False,
        "ztdr": False,
        # Validity (higher better — more grounded, less hallucinated)
        "citation_grounding_rate": True,
        "entity_existence_rate": True,
        "kubectl_actionability_rate": True,
    },
)


# --------------------------------------------------------------------------- #
# Adapter                                                                     #
# --------------------------------------------------------------------------- #


class CloudOpsBenchAdapter(BenchmarkAdapter):
    """The first ``BenchmarkAdapter`` — CloudOpsBench K8s scenarios.

    Usage::

        adapter = CloudOpsBenchAdapter()
        for case in adapter.load_cases(CaseFilters(limit=5, seed=42)):
            alert = adapter.build_alert(case)
            integrations = adapter.build_opensre_integrations(case)
            # ... runner invokes opensre, builds RunResult ...
            score = adapter.score_case(case, run_result)
    """

    name = "cloudopsbench"
    version = "1.0.0"

    def __init__(self, benchmark_dir: Path = BENCHMARK_DIR) -> None:
        self._benchmark_dir = benchmark_dir
        # CloudOpsCase cache so we don't re-load case files between
        # build_alert / build_opensre_integrations / score_case for the same case.
        # Mutated only from load_cases (single-threaded before parallel runs
        # start); read-only during cell execution → safe for the framework
        # runner's ThreadPoolExecutor.
        self._cases_by_id: dict[str, CloudOpsCase] = {}

    # ----------------------------------------------------------------------- #
    # BenchmarkAdapter interface                                              #
    # ----------------------------------------------------------------------- #

    def load_cases(self, filters: CaseFilters) -> Iterator[BenchmarkCase]:
        """Stream cases matching the filter, with seeded random selection
        when ``filters.seed`` is set.

        Filter mapping:
            ``filters.systems[0]`` → ``system_filter``  (only first used; legacy limit)
            ``filters.fault_categories[0]`` → ``fault_category_filter``
            ``filters.case_ids[0]`` → ``case_filter``
            ``filters.limit`` → applied AFTER seeded sample so randomization is fair
            ``filters.seen_shape`` → applied AFTER tagging (Phase D); each case
                gets ``seen_shape`` from :func:`tags.seen_shape_for`

        For multi-value filters (e.g., multiple systems), call this method
        once per value and merge — current case_loader doesn't support OR.
        """
        legacy_cases = list(
            _legacy_load_cases(
                benchmark_dir=self._benchmark_dir,
                system=filters.systems[0] if filters.systems else None,
                fault_category=(filters.fault_categories[0] if filters.fault_categories else None),
                case_name=filters.case_ids[0] if filters.case_ids else None,
                limit=None,  # we apply limit below after random sample
            )
        )

        # Held-out 20% set — computed against the FULL filter-loaded corpus
        # so the split is stable regardless of seen-shape / limit filtering
        # applied later. Integrity Mechanism 8 (generalization gate).
        held_out_ids = compute_held_out_set(c.case_id for c in legacy_cases)

        # Seeded random selection — integrity Mechanism 6 (no cherry-picking)
        if filters.seed is not None:
            rng = random.Random(filters.seed)
            rng.shuffle(legacy_cases)

        # Apply seen/unseen filter BEFORE limit so `limit=N` means
        # "N matching cases", not "N candidates, some of which match"
        wanted_seen_shape: set[bool] | None = (
            set(filters.seen_shape) if filters.seen_shape else None
        )
        if wanted_seen_shape is not None:
            legacy_cases = [
                c for c in legacy_cases if seen_shape_for(c.fault_category) in wanted_seen_shape
            ]

        # Apply limit after shape filtering so the sample is uniform random
        # over the filtered subset
        if filters.limit is not None and filters.limit > 0:
            legacy_cases = legacy_cases[: filters.limit]

        for legacy in legacy_cases:
            seen_shape = seen_shape_for(legacy.fault_category)
            self._cases_by_id[legacy.case_id] = legacy
            yield BenchmarkCase(
                case_id=legacy.case_id,
                benchmark_name=self.name,
                metadata={
                    "system": legacy.system,
                    "fault_category": legacy.fault_category,
                    "case_name": legacy.case_name,
                    "namespace": legacy.namespace,
                    "query": legacy.query,
                    "ground_truth": asdict(legacy.result),
                    "process": legacy.process,
                    "is_held_out": legacy.case_id in held_out_ids,
                },
                seen_shape=seen_shape,
            )

    def build_alert(self, case: BenchmarkCase) -> AlertPayload:
        """Wrap the legacy build_alert in the framework's AlertPayload shape."""
        legacy = self._require_case(case)
        raw = _legacy_build_alert(legacy)
        return AlertPayload(
            raw=raw,
            normalized={
                "system": legacy.system,
                "fault_category": legacy.fault_category,
                "namespace": legacy.namespace,
                "query": legacy.query,
            },
        )

    def build_opensre_integrations(self, case: BenchmarkCase) -> dict[str, Any]:
        """Construct a fresh State Snapshot replay backend per case and
        wire it under the ``eks`` integration key opensre's CloudOpsBench
        tools (``app/tools/CloudOpsBenchK8sTools/__init__.py``) read from.

        The returned dict is the only place this cell's backend lives;
        the runner passes it back via ``RunContext`` to ``score_case``.
        Stateless on the adapter — safe for parallel execution.

        NOTE: ``run_suite._build_resolved_integrations`` placed the backend
        under the ``aws`` key, which doesn't match what the CloudOpsBench
        tools look for. As a result the legacy benchmark agent has been
        completing investigations without ever calling the State Snapshot
        tools. This adapter fixes the key (uses ``eks``); the legacy
        ``run_suite.py`` will be removed by the framework rollout.
        """
        legacy = self._require_case(case)
        backend = CloudOpsBenchReplayBackend(legacy)
        cluster_name = f"cloudopsbench-{legacy.system}"
        return {
            # Useful for AWS-region-aware tools; not where the backend lives.
            "aws": {
                "role_arn": "",
                "external_id": "",
                "region": "us-east-1",
                "cluster_names": [cluster_name],
            },
            # CloudOpsBenchK8sTools read from here (sources["eks"]["_backend"]).
            "eks": {
                "namespace": legacy.namespace,
                "cluster_name": cluster_name,
                "_backend": backend,
            },
        }

    def build_baseline_tools(self, case: BenchmarkCase) -> dict[str, Any]:
        """LLM-alone mode is implemented in Phase B (separate workstream).

        Raises NotImplementedError so the runner fails fast and clearly
        rather than silently scoring a baseline run as opensre.
        """
        raise NotImplementedError(
            "build_baseline_tools is Phase B of the task scope — "
            "see opensre-benchmark-task-scope.md. Until then, run with "
            "modes=['opensre+llm'] only."
        )

    def score_case(self, case: BenchmarkCase, run: RunResult, context: RunContext) -> CaseScore:
        """Score the case using CloudOpsBench's 15 paper metrics.

        Reads the replay backend out of ``context.integrations`` — the same
        dict ``build_opensre_integrations`` returned for THIS cell. No
        per-cell state on the adapter (thread-safe).
        """
        legacy = self._require_case(case)
        backend = (context.integrations.get("eks") or {}).get("_backend")
        if not isinstance(backend, CloudOpsBenchReplayBackend):
            return CaseScore(
                case_id=case.case_id,
                metrics={},
                failure_reason=(
                    "context.integrations missing 'eks._backend' of type "
                    "CloudOpsBenchReplayBackend — runner must pass the same "
                    "integrations dict to score_case as it passed to run_investigation"
                ),
            )

        case_data = _build_case_data(legacy, backend, run)
        legacy_score = _legacy_score_case(legacy, case_data)

        # Combine paper metrics + new validity metrics (Phase C)
        metrics: dict[str, float] = dict(asdict(legacy_score.metrics))
        finding_text = (
            str(run.final_diagnosis.get("report") or "")
            + "\n"
            + str(run.final_diagnosis.get("root_cause") or "")
        )
        metrics["citation_grounding_rate"] = compute_citation_grounding(
            finding_text, run.evidence_entries
        )
        metrics["entity_existence_rate"] = compute_entity_existence(
            finding_text, backend, legacy.namespace
        )
        metrics["kubectl_actionability_rate"] = compute_kubectl_actionability(finding_text)

        return CaseScore(case_id=case.case_id, metrics=metrics)

    def metric_schema(self) -> MetricSchema:
        """The paper's 15 metrics. Validity metrics arrive in Phase C."""
        return _PAPER_METRIC_SCHEMA

    # ----------------------------------------------------------------------- #
    # Internal                                                                #
    # ----------------------------------------------------------------------- #

    def _require_case(self, case: BenchmarkCase) -> CloudOpsCase:
        """Retrieve the cached legacy case; raise if absent.

        The cache is populated by ``load_cases``. Calling other adapter
        methods with a case that wasn't loaded through us is a programming
        error.
        """
        if case.case_id not in self._cases_by_id:
            raise KeyError(
                f"case {case.case_id!r} was not produced by this adapter's "
                f"load_cases — adapter methods can only be called with cases "
                f"this adapter yielded"
            )
        return self._cases_by_id[case.case_id]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _build_case_data(
    legacy: CloudOpsCase,
    backend: CloudOpsBenchReplayBackend,
    run: RunResult,
) -> dict[str, Any]:
    """Convert a framework RunResult into the dict the legacy scorer expects.

    The legacy ``score_case(case, case_data)`` reads case_data from the
    payload that ``run_suite.run_case`` builds. We replicate that shape
    here so the legacy scorer works unchanged.
    """
    return {
        "case_id": legacy.case_id,
        "system": legacy.system,
        "fault_category": legacy.fault_category,
        "case_name": legacy.case_name,
        "ground_truth": {
            "fault_taxonomy": legacy.result.fault_taxonomy,
            "fault_object": legacy.result.fault_object,
            "root_cause": legacy.result.root_cause,
        },
        "final_answer": run.final_diagnosis,
        "root_cause": run.final_diagnosis.get("root_cause"),
        "report": run.final_diagnosis.get("report"),
        "expert_steps": {
            "path1": list(legacy.process.get("path1") or []),
            "path2": list(legacy.process.get("path2") or []),
        },
        "steps": _steps_from_backend(backend),
        # The legacy scorer doesn't require final_state, but pass it through
        # for forward-compat with future scoring extensions.
        "final_state": {"evidence_entries": run.evidence_entries},
    }


def _steps_from_backend(backend: CloudOpsBenchReplayBackend) -> list[dict[str, Any]]:
    """Convert backend.action_log into the step list shape legacy scoring expects.

    Mirrors ``run_suite._steps_from_backend`` so legacy scoring works on
    framework-produced runs without changes.
    """
    steps: list[dict[str, Any]] = []
    for idx, entry in enumerate(backend.action_log, start=1):
        steps.append(
            {
                "step_id": idx,
                "action_type": "tool",
                "action_name": entry.get("action_name"),
                "action_input": entry.get("action_input", {}),
                "error": entry.get("error"),
                "tool_latency": 0.0,
            }
        )
    return steps
