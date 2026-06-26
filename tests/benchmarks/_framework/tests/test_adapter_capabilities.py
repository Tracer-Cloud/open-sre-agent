"""Tests for the AdapterCapabilities flag layer.

These tests pin two behaviors that replace the previous hardcoded
``if config.benchmark != "cloudopsbench"`` guards in ``config.py``:

  1. Adapters declare what framework features they support via a
     ``capabilities: ClassVar[AdapterCapabilities]`` class attribute.
     Default is all-False so a new adapter is locked down until it opts
     in deliberately.

  2. The framework consults the adapter's capabilities — not the
     adapter's name — to decide whether config knobs like
     ``agent_variant`` and ``predictor_variant`` are accepted.

The pattern unblocks adding new adapters (OpenRCA, ToolCallBench, etc.)
without touching framework validation code.
"""

from __future__ import annotations

import pytest

from tests.benchmarks._framework.adapter_base import (
    AdapterCapabilities,
    BenchmarkAdapter,
)
from tests.benchmarks._framework.registry import capabilities_for

# --------------------------------------------------------------------------- #
# AdapterCapabilities model contract                                          #
# --------------------------------------------------------------------------- #


def test_default_capabilities_lock_down_all_features() -> None:
    """A new adapter that doesn't override ``capabilities`` gets the
    all-False default — every gated feature is refused until opted in.
    This is the safe default; it prevents a new adapter from silently
    inheriting framework features it doesn't actually implement."""
    caps = AdapterCapabilities()
    assert caps.supports_agent_variant is False
    assert caps.supports_predictor_variant is False


def test_capabilities_model_is_frozen() -> None:
    """An adapter's declared capabilities must be immutable for the
    lifetime of the process. Mutating capabilities at runtime would
    let a misbehaving adapter (or a test) toggle a feature mid-run
    and bypass config validation. ``frozen=True`` forbids field
    assignment after construction."""
    caps = AdapterCapabilities(supports_agent_variant=True)
    with pytest.raises((TypeError, ValueError)):
        caps.supports_agent_variant = False  # type: ignore[misc]


def test_capabilities_model_forbids_extra_fields() -> None:
    """A typo in a capability name (``support_agent_variant`` missing the
    plural ``s``) must error at construction time rather than silently
    create an inert flag. ``extra='forbid'`` makes the schema closed."""
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        AdapterCapabilities(support_agent_variant=True)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# BenchmarkAdapter integration                                                #
# --------------------------------------------------------------------------- #


def test_benchmarkadapter_default_capabilities_are_all_false() -> None:
    """Adapter subclasses that don't override ``capabilities`` inherit
    the all-False default from the ABC. Pin so a future ABC refactor
    doesn't accidentally enable features by default."""
    # We can't instantiate the abstract class directly, so read the
    # class attribute. ``ClassVar`` makes this the source of truth.
    caps = BenchmarkAdapter.capabilities
    assert caps.supports_agent_variant is False
    assert caps.supports_predictor_variant is False


def test_cloudopsbench_adapter_declares_both_capabilities() -> None:
    """CloudOpsBench's adapter is the bench's current truth source for
    ``agent_variant`` + ``predictor_variant``. Pin both flags so a
    refactor that mistakenly drops the capability declaration would
    immediately fail this test (and the cross-field config guards
    would start refusing valid CloudOpsBench configs)."""
    from tests.benchmarks.cloudopsbench.adapter import CloudOpsBenchAdapter

    caps = CloudOpsBenchAdapter.capabilities
    assert caps.supports_agent_variant is True
    assert caps.supports_predictor_variant is True


# --------------------------------------------------------------------------- #
# Registry helper                                                             #
# --------------------------------------------------------------------------- #


def test_capabilities_for_returns_cloudopsbench_capabilities() -> None:
    """``capabilities_for("cloudopsbench")`` returns the same capability
    object the adapter class declares, so framework code can read flags
    from the registry instead of importing the adapter directly."""
    from tests.benchmarks.cloudopsbench.adapter import CloudOpsBenchAdapter

    caps = capabilities_for("cloudopsbench")
    assert caps == CloudOpsBenchAdapter.capabilities


def test_capabilities_for_raises_keyerror_for_unknown_adapter() -> None:
    """An unknown benchmark name surfaces with a helpful KeyError that
    lists the registered adapters — same UX contract as ``build_adapter``."""
    with pytest.raises(KeyError, match="known adapters"):
        capabilities_for("not-a-real-benchmark")


def test_capabilities_for_does_not_instantiate_class_factory() -> None:
    """Performance + no-side-effect contract: when the registered factory
    is the adapter class itself (the common pattern, e.g.
    ``register_adapter("X", XAdapter)``), ``capabilities_for`` must read
    the ClassVar directly without calling ``__init__``. Adapter __init__
    can do real work (HF dataset loads, replay backend setup); running
    that during config lint would be wasted work at best, surprising
    side effects at worst."""
    from tests.benchmarks._framework.adapter_base import (
        AdapterCapabilities,
        BenchmarkAdapter,
    )
    from tests.benchmarks._framework.registry import (
        capabilities_for,
        register_adapter,
    )

    init_calls: list[int] = []

    class _NoInitAdapter(BenchmarkAdapter):
        """Adapter whose __init__ records every invocation. The
        capability lookup must NEVER append to this list."""

        name = "test-no-init-adapter"
        version = "0.0.0"
        capabilities = AdapterCapabilities(supports_agent_variant=True)

        def __init__(self) -> None:
            init_calls.append(1)

        # The abstract surface — stubs are fine for this contract test;
        # capabilities_for must never reach them. The unused-arg noqa
        # comments document the intent: these are deliberate trip-wires,
        # not signatures the test exercises.
        def load_cases(self, filters):  # type: ignore[no-untyped-def]  # noqa: ARG002
            raise AssertionError("load_cases reached")

        def build_alert(self, case):  # type: ignore[no-untyped-def]  # noqa: ARG002
            raise AssertionError("build_alert reached")

        def build_opensre_integrations(self, case):  # type: ignore[no-untyped-def]  # noqa: ARG002
            raise AssertionError("build_opensre_integrations reached")

        def build_baseline_tools(self, case):  # type: ignore[no-untyped-def]  # noqa: ARG002
            raise AssertionError("build_baseline_tools reached")

        def score_case(self, case, run, context):  # type: ignore[no-untyped-def]  # noqa: ARG002
            raise AssertionError("score_case reached")

        def metric_schema(self):  # type: ignore[no-untyped-def]
            raise AssertionError("metric_schema reached")

    register_adapter(_NoInitAdapter.name, _NoInitAdapter)
    try:
        caps = capabilities_for(_NoInitAdapter.name)
        assert caps.supports_agent_variant is True
        assert init_calls == [], (
            f"capabilities_for unexpectedly instantiated the adapter "
            f"({len(init_calls)} __init__ call(s))"
        )
    finally:
        # Clean up the test-only registration so other tests see a clean slate.
        from tests.benchmarks._framework.registry import _ADAPTER_FACTORIES

        _ADAPTER_FACTORIES.pop(_NoInitAdapter.name, None)
