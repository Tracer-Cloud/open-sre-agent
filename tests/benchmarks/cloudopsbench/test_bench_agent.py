"""Tests for the CloudOpsBench-specific investigation agent subclass."""

from __future__ import annotations

import pytest

from app.agent.investigation import ConnectedInvestigationAgent
from app.tools.registered_tool import RegisteredTool
from tests.benchmarks.cloudopsbench.bench_agent import BenchInvestigationAgent


def _make_registered_tool(name: str, origin_module: str) -> RegisteredTool:
    """Build a minimal RegisteredTool stub for tool-filter tests.

    We don't exercise the tool — only the registry metadata used by
    ``_filter_tools`` — so the schema/run callable can be no-op placeholders.
    """
    return RegisteredTool(
        name=name,
        description=f"Stub tool {name}",
        input_schema={"type": "object"},
        source="eks",
        run=lambda **_: None,
        origin_module=origin_module,
    )


def test_bench_agent_is_subclass_of_production_agent() -> None:
    """Subclass relationship is what lets the bench inject its agent via the
    pipeline's ``agent_class`` parameter without production code knowing."""
    assert issubclass(BenchInvestigationAgent, ConnectedInvestigationAgent)


def test_bench_agent_blocks_conclusion_below_floor() -> None:
    """Below MIN_TOOL_CALLS: reject conclusion + emit nudge user message.
    This is the entire point of the subclass — gpt-5 was bailing after 4
    tool calls on the June-3 run; the floor forces deeper investigation."""
    agent = BenchInvestigationAgent()
    accept, nudge = agent._should_accept_conclusion(evidence_count=3, iteration=2)
    assert accept is False
    assert nudge is not None
    # Nudge text must mention the count so the model knows where it stands.
    assert "3 tool result" in nudge


def test_bench_agent_allows_conclusion_at_threshold() -> None:
    """Exactly MIN_TOOL_CALLS evidence entries → accept conclusion. The
    threshold is INCLUSIVE so the agent isn't forced to do extra calls
    when it has already met the floor."""
    agent = BenchInvestigationAgent()
    accept, nudge = agent._should_accept_conclusion(
        evidence_count=BenchInvestigationAgent.MIN_TOOL_CALLS,
        iteration=BenchInvestigationAgent.MIN_TOOL_CALLS,
    )
    assert accept is True
    assert nudge is None


def test_bench_agent_allows_conclusion_above_threshold() -> None:
    agent = BenchInvestigationAgent()
    accept, nudge = agent._should_accept_conclusion(
        evidence_count=BenchInvestigationAgent.MIN_TOOL_CALLS + 10, iteration=8
    )
    assert accept is True
    assert nudge is None


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4, 5, 6, 7])
def test_bench_agent_rejects_below_floor_for_every_count_under_min(count: int) -> None:
    """Exhaustive: every count below the floor must be rejected. Guards
    against a future off-by-one in the floor comparison."""
    agent = BenchInvestigationAgent()
    accept, nudge = agent._should_accept_conclusion(evidence_count=count, iteration=count)
    assert accept is False
    assert nudge is not None


def test_bench_agent_threshold_is_class_attribute_for_easy_override() -> None:
    """The threshold is a class attribute so a future bench (or one-off
    experiment) can subclass and tweak it without rebuilding the agent
    instance or duplicating the hook method."""

    class _RelaxedBench(BenchInvestigationAgent):
        MIN_TOOL_CALLS = 3

    agent = _RelaxedBench()
    accept, _ = agent._should_accept_conclusion(evidence_count=3, iteration=2)
    assert accept is True


def test_production_agent_filter_tools_is_identity() -> None:
    """Default ``_filter_tools`` must return the input unchanged so production
    investigations continue to see every available tool. Regressing this
    silently disables tools across the entire product."""
    tools = [
        _make_registered_tool("EKSListClusters", "app.tools.EKSListClustersTool"),
        _make_registered_tool("HermesLogs", "app.tools.HermesLogsTool"),
    ]
    assert ConnectedInvestigationAgent()._filter_tools(tools) == tools


def test_bench_agent_filter_keeps_only_bench_package_tools() -> None:
    """The bench agent must hide production opensre tools that would hit
    real AWS / Hermes endpoints the bench task role cannot reach. Whitelist
    is by origin_module prefix so a new bench tool added under
    ``tests/benchmarks/cloudopsbench/tools/`` is picked up automatically."""
    bench_tool = _make_registered_tool("GetResources", "tests.benchmarks.cloudopsbench.tools.k8s")
    prod_eks = _make_registered_tool("EKSListClusters", "app.tools.EKSListClustersTool")
    prod_hermes = _make_registered_tool("HermesLogs", "app.tools.HermesLogsTool")
    filtered = BenchInvestigationAgent()._filter_tools([bench_tool, prod_eks, prod_hermes])
    assert filtered == [bench_tool]


def test_bench_agent_filter_drops_everything_when_no_bench_tools_registered() -> None:
    """Defensive: if the bench package failed to register (import error,
    missing module) and only production tools are visible, the bench agent
    should return an empty list rather than fall back to production tools.
    The ``run`` loop already logs a warning when no tools are available."""
    only_prod = [
        _make_registered_tool("EKSListClusters", "app.tools.EKSListClustersTool"),
        _make_registered_tool("HermesLogs", "app.tools.HermesLogsTool"),
    ]
    assert BenchInvestigationAgent()._filter_tools(only_prod) == []


def test_bench_agent_allowed_prefixes_is_class_attribute_for_override() -> None:
    """Mirrors the MIN_TOOL_CALLS convention — a one-off experiment can
    widen or narrow the prefix tuple without rebuilding the instance or
    duplicating the hook method."""

    class _MixedBench(BenchInvestigationAgent):
        ALLOWED_TOOL_MODULE_PREFIXES = (
            "tests.benchmarks.cloudopsbench.tools.",
            "app.tools.EKSListClustersTool",  # add a single production tool
        )

    bench_tool = _make_registered_tool("GetResources", "tests.benchmarks.cloudopsbench.tools.k8s")
    prod_eks = _make_registered_tool("EKSListClusters", "app.tools.EKSListClustersTool")
    prod_hermes = _make_registered_tool("HermesLogs", "app.tools.HermesLogsTool")
    filtered = _MixedBench()._filter_tools([bench_tool, prod_eks, prod_hermes])
    assert filtered == [bench_tool, prod_eks]


def test_bench_agent_filter_drops_tool_at_prefix_root_without_submodule() -> None:
    """Trailing-dot guard: a tool whose origin_module equals the prefix
    ROOT (no submodule) is dropped, matching the comment on
    ``_BENCH_TOOL_MODULE_PREFIX``. The registry's pkgutil walk never
    produces this state for auto-discovered tools, but a direct
    ``register_external_tool_package`` against a single-file module would —
    the test pins the documented behavior so future contributors who hit
    it can find this commit."""
    at_root = _make_registered_tool(
        "AtRoot",
        "tests.benchmarks.cloudopsbench.tools",  # no trailing submodule
    )
    bench_submodule = _make_registered_tool(
        "GetResources", "tests.benchmarks.cloudopsbench.tools.k8s"
    )
    filtered = BenchInvestigationAgent()._filter_tools([at_root, bench_submodule])
    assert filtered == [bench_submodule]


def test_bench_agent_filter_warns_on_empty_origin_module(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``origin_module`` defaults to "" on directly-constructed RegisteredTool.
    Such a tool is dropped silently if we don't surface it — the bench
    would quietly run with fewer tools and the user would never know.
    We warn at WARNING so the registry bug shows up in the run log."""
    no_origin = _make_registered_tool("Orphan", "")
    bench_tool = _make_registered_tool("GetResources", "tests.benchmarks.cloudopsbench.tools.k8s")
    with caplog.at_level("WARNING"):
        filtered = BenchInvestigationAgent()._filter_tools([no_origin, bench_tool])
    assert filtered == [bench_tool]
    # The warning must name the offending tool so an operator can find it
    # in the registry.
    assert any("Orphan" in r.message and "empty origin_module" in r.message for r in caplog.records)
