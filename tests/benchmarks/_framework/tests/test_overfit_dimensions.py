"""Tests for the OverfitDimensions adapter hook.

Phase 3 of the framework decoupling moved the metadata key names that
``_framework/overfit.py`` guards consult out of inline ``["system"]``
/ ``["fault_category"]`` / ``["fault_object"]`` literals and into an
``OverfitDimensions`` model the adapter declares.

The defaults still match CloudOpsBench's schema for backward
compatibility — every pre-existing call to ``per_system_uniformity``,
``per_stratum_uniformity``, ``flipped_loss_to_win_clusters``, and
``analyze`` continues to work without changes. The hook adds the
*ability* for a non-CloudOpsBench adapter to declare different keys
without the framework knowing about either schema.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.benchmarks._framework.adapter_base import (
    BenchmarkAdapter,
    OverfitDimensions,
)
from tests.benchmarks._framework.overfit import (
    analyze,
    flipped_loss_to_win_clusters,
    per_stratum_uniformity,
    per_system_uniformity,
)

# --------------------------------------------------------------------------- #
# OverfitDimensions model contract                                            #
# --------------------------------------------------------------------------- #


def test_default_dimensions_match_cloudopsbench_schema() -> None:
    """Defaults preserve back-compat for every pre-existing call site.
    CloudOpsBench's metadata layout is the framework default; adapters
    with the same shape don't need to override the hook."""
    dims = OverfitDimensions()
    assert dims.system_key == "system"
    assert dims.stratum_key == "fault_category"
    assert dims.gt_object_key == "fault_object"


def test_dimensions_model_is_frozen() -> None:
    """The dimensions an adapter declares must be immutable for the
    lifetime of the process — mutating them mid-run could let a guard
    read a different key on different cases. Same ``frozen=True``
    constraint as ``AdapterCapabilities``."""
    dims = OverfitDimensions(system_key="cluster")
    with pytest.raises((TypeError, ValueError)):
        dims.system_key = "namespace"  # type: ignore[misc]


def test_dimensions_model_forbids_extra_fields() -> None:
    """A typo in a dimension name (``system_kye``) must error at
    construction time rather than silently create an inert key."""
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        OverfitDimensions(system_kye="anything")  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# BenchmarkAdapter hook                                                       #
# --------------------------------------------------------------------------- #


def test_benchmarkadapter_default_overfit_dimensions_match_defaults() -> None:
    """The ABC's default ``overfit_dimensions`` returns the all-default
    instance. An adapter that doesn't override the hook gets the
    CloudOpsBench-schema defaults — works out of the box for any
    adapter that follows the same metadata convention."""

    class _StubAdapter(BenchmarkAdapter):
        name = "test-stub"
        version = "0.0.0"

        def load_cases(self, filters):  # type: ignore[no-untyped-def]  # noqa: ARG002
            raise AssertionError("not called")

        def build_alert(self, case):  # type: ignore[no-untyped-def]  # noqa: ARG002
            raise AssertionError("not called")

        def build_opensre_integrations(self, case):  # type: ignore[no-untyped-def]  # noqa: ARG002
            raise AssertionError("not called")

        def build_baseline_tools(self, case):  # type: ignore[no-untyped-def]  # noqa: ARG002
            raise AssertionError("not called")

        def score_case(self, case, run, context):  # type: ignore[no-untyped-def]  # noqa: ARG002
            raise AssertionError("not called")

        def metric_schema(self):  # type: ignore[no-untyped-def]
            raise AssertionError("not called")

    dims = _StubAdapter().overfit_dimensions()
    assert dims == OverfitDimensions()


def test_cloudopsbench_adapter_inherits_default_dimensions() -> None:
    """CloudOpsBench's metadata layout matches the defaults exactly
    (``system`` / ``fault_category`` / ``fault_object``), so the
    adapter inherits the base ABC implementation. Pin so a future
    refactor that changes the defaults would surface here."""
    from tests.benchmarks.cloudopsbench.adapter import CloudOpsBenchAdapter

    adapter = CloudOpsBenchAdapter.__new__(CloudOpsBenchAdapter)
    assert adapter.overfit_dimensions() == OverfitDimensions()


# --------------------------------------------------------------------------- #
# Guards honour custom dimensions                                             #
# --------------------------------------------------------------------------- #


def _cell_custom_keys(
    case_id: str,
    *,
    cluster: str,
    category: str,
    a1: float,
) -> dict[str, Any]:
    """Build a cell whose metadata uses non-CloudOpsBench key names —
    ``cluster`` instead of ``system``, ``category`` instead of
    ``fault_category``. Forces the guard to use the adapter-declared
    keys rather than the hardcoded CloudOpsBench literals."""
    return {
        "case": {
            "case_id": case_id,
            "metadata": {
                "cluster": cluster,
                "category": category,
                "ground_truth": {"target": f"app/{cluster}-svc"},
            },
        },
        "run": {"mode": "opensre+llm", "run_index": 0},
        "score": {"metrics": {"a1": a1}},
    }


def test_per_system_uniformity_uses_custom_system_key() -> None:
    """Pass ``OverfitDimensions(system_key="cluster")`` and the guard
    must read ``case.metadata["cluster"]`` instead of the default
    ``case.metadata["system"]``. Without this hook, cells whose
    metadata doesn't have a ``system`` key would KeyError at scoring
    time."""
    dims = OverfitDimensions(system_key="cluster", stratum_key="category", gt_object_key="target")
    baseline = [
        _cell_custom_keys("s1", cluster="east", category="net", a1=0.0),
        _cell_custom_keys("s2", cluster="west", category="net", a1=0.0),
    ]
    variant = [
        _cell_custom_keys("s1", cluster="east", category="net", a1=1.0),
        _cell_custom_keys("s2", cluster="west", category="net", a1=1.0),
    ]
    verdict = per_system_uniformity(baseline, variant, "opensre+llm", dimensions=dims)
    # Both "clusters" lifted identically, so spread=0 and the guard passes.
    assert verdict.passed is True
    detail_systems = sorted(verdict.detail["per_system"].keys())
    assert detail_systems == ["east", "west"]


def test_per_stratum_uniformity_uses_custom_stratum_key() -> None:
    """Symmetric: ``stratum_key="category"`` makes the guard read
    ``case.metadata["category"]`` instead of ``fault_category``."""
    dims = OverfitDimensions(system_key="cluster", stratum_key="category", gt_object_key="target")
    baseline = [
        _cell_custom_keys("s1", cluster="east", category="alpha", a1=0.0),
        _cell_custom_keys("s2", cluster="east", category="beta", a1=0.0),
    ]
    variant = [
        _cell_custom_keys("s1", cluster="east", category="alpha", a1=1.0),
        _cell_custom_keys("s2", cluster="east", category="beta", a1=1.0),
    ]
    verdict = per_stratum_uniformity(baseline, variant, "opensre+llm", dimensions=dims)
    assert verdict.passed is True
    assert set(verdict.detail["per_stratum"].keys()) == {"alpha", "beta"}


def test_cluster_concentration_uses_custom_keys_for_cluster_key() -> None:
    """Guard C's cluster key is ``(system, stratum, gt_object_prefix)``.
    All three components come from the dimensions model; pin that the
    cluster fingerprint honours every override AND that the OUTPUT
    schema labels each component with the adapter's dimension key name.

    Prior to Phase 3 the output dict was hardcoded as
    ``{"system": ..., "fault_category": ..., "gt_prefix": ...}`` —
    correct for CloudOpsBench but silently misleading for any adapter
    whose dimension names differ. The current shape uses
    ``dims.system_key`` / ``dims.stratum_key`` as the actual key names
    so a ``cluster``-shaped adapter sees a ``cluster``-shaped report.
    """
    dims = OverfitDimensions(system_key="cluster", stratum_key="category", gt_object_key="target")
    baseline = [_cell_custom_keys("s1", cluster="east", category="alpha", a1=0.0)]
    variant = [_cell_custom_keys("s1", cluster="east", category="alpha", a1=1.0)]
    verdict = flipped_loss_to_win_clusters(baseline, variant, "opensre+llm", dimensions=dims)
    assert verdict.detail["total_flips"] == 1
    top = verdict.detail["top_clusters"][0]
    # Adapter-aligned output: keys come from the dimensions model.
    assert top["cluster"] == "east"
    assert top["category"] == "alpha"
    # CloudOpsBench-only legacy labels must NOT appear when a non-default
    # adapter is in use — that was the silent-misrepresentation bug.
    assert "system" not in top
    assert "fault_category" not in top


def test_analyze_forwards_dimensions_to_guards() -> None:
    """The aggregator must forward ``dimensions`` to every guard that
    consults case metadata. A run with non-default keys must analyze
    end-to-end without any guard crashing on a missing literal key."""
    dims = OverfitDimensions(system_key="cluster", stratum_key="category", gt_object_key="target")
    baseline = [
        _cell_custom_keys("s1", cluster="east", category="alpha", a1=0.0),
        _cell_custom_keys("s2", cluster="west", category="beta", a1=0.0),
    ]
    variant = [
        _cell_custom_keys("s1", cluster="east", category="alpha", a1=1.0),
        _cell_custom_keys("s2", cluster="west", category="beta", a1=1.0),
    ]
    # analyze() returns a report; the call must complete without raising,
    # which is the load-bearing contract here.
    report = analyze(baseline, variant, mode="opensre+llm", dimensions=dims)
    assert report.full_corpus_n == 2
