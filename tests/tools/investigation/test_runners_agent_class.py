"""Regression tests for ``agent_class`` threading through the pipeline.

The investigation pipeline exposes an ``agent_class`` parameter at two
public surfaces:

  - :func:`tools.investigation.capability.run_investigation`
  - :func:`tools.investigation.lifecycle.run_connected_investigation`
    (deprecated facade over :func:`tools.investigation.agent_pipeline.run_agent_investigation`)

The parameter MUST thread cleanly from the outer ``run_investigation``
all the way to where the agent is constructed, so callers (e.g. test
suites, downstream integrators) can substitute a subclass without
patching internals.

These tests pin that contract — a future refactor that drops the
parameter or stops forwarding it would fail loudly here instead of
silently using the production default.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest

from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent


class _SentinelAgent(ConnectedInvestigationAgent):
    """Records whether its constructor + run were invoked."""

    instances_constructed: list[_SentinelAgent] = []

    def __init__(self) -> None:
        super().__init__()
        _SentinelAgent.instances_constructed.append(self)
        self.was_run = False

    def run(  # type: ignore[override]
        self,
        state: dict[str, Any],  # noqa: ARG002 — base signature
        on_event: Any | None = None,  # noqa: ARG002 — base signature
    ) -> dict[str, Any]:
        self.was_run = True
        # Return a benign updates dict — the pipeline merges it into state.
        return {"sentinel_agent_ran": True}


def _reset_sentinel() -> None:
    _SentinelAgent.instances_constructed.clear()


@contextmanager
def _stubbed_stages() -> Iterator[None]:
    """Stub every pipeline stage except the investigation agent itself."""
    with (
        patch(
            "tools.investigation.stages.resolve_integrations.resolve_integrations",
            return_value={"resolved_integrations": {}},
        ),
        patch(
            "tools.investigation.stages.intake.extract_alert",
            return_value={"is_noise": False},
        ),
        patch("tools.investigation.stages.plan_evidence.plan_actions", return_value={}),
        patch(
            "tools.investigation.reporting.upstream_correlation.node.node_correlate_upstream",
            return_value={},
        ),
        patch("tools.investigation.reporting.deliver", return_value={}),
    ):
        yield


def test_run_connected_investigation_uses_agent_class_when_provided() -> None:
    """The pipeline must instantiate the override class, not the default."""
    _reset_sentinel()
    from tools.investigation.lifecycle import run_connected_investigation
    from tools.investigation.state_factory import make_initial_state

    state = make_initial_state(raw_alert="alert text")
    with _stubbed_stages(), pytest.warns(DeprecationWarning, match="deprecated"):
        run_connected_investigation(state, agent_class=_SentinelAgent)

    assert len(_SentinelAgent.instances_constructed) == 1
    assert _SentinelAgent.instances_constructed[0].was_run is True


@pytest.mark.parametrize("cli_backed", [False, True])
def test_run_connected_investigation_picks_default_policy_from_routing(cli_backed: bool) -> None:
    """Omitting ``agent_class`` selects the policy the configured transport implies."""
    from tools.investigation.lifecycle import run_connected_investigation
    from tools.investigation.stages.gather_evidence.agent import CLIBackedInvestigationAgent
    from tools.investigation.state_factory import make_initial_state

    state = make_initial_state(raw_alert="alert text")
    # Patched on the base class so an instance of the wrong policy is still
    # recorded — the assertion is which class was constructed, not that some
    # agent ran.
    with (
        _stubbed_stages(),
        pytest.warns(DeprecationWarning, match="deprecated"),
        patch(
            "core.agent_harness.runtime.agent_llm_is_cli_backed",
            return_value=cli_backed,
        ),
        patch.object(
            ConnectedInvestigationAgent, "run", autospec=True, return_value={}
        ) as mock_run,
    ):
        run_connected_investigation(state)  # no agent_class kwarg

    expected = CLIBackedInvestigationAgent if cli_backed else ConnectedInvestigationAgent
    assert type(mock_run.call_args.args[0]) is expected


@pytest.mark.anyio
@pytest.mark.parametrize("cli_backed", [False, True])
async def test_astream_investigation_picks_default_policy_from_routing(cli_backed: bool) -> None:
    """Interactive streaming must use the same CLI-backed vs hosted policy as the sync path."""
    from tools.investigation.capability import astream_investigation
    from tools.investigation.stages.gather_evidence.agent import CLIBackedInvestigationAgent

    with (
        patch(
            "tools.investigation.stages.resolve_integrations.resolve_integrations",
            return_value={"resolved_integrations": {}},
        ),
        patch(
            "tools.investigation.stages.intake.extract_alert",
            return_value={"is_noise": False, "alert_name": "t", "severity": "warning"},
        ),
        patch("tools.investigation.stages.plan_evidence.plan_actions", return_value={}),
        patch("core.agent_harness.runtime.agent_llm_is_cli_backed", return_value=cli_backed),
        patch.object(
            ConnectedInvestigationAgent, "run", autospec=True, return_value={}
        ) as mock_run,
        patch("tools.investigation.stages.diagnose.diagnose", return_value={}),
        patch(
            "tools.investigation.reporting.upstream_correlation.node.node_correlate_upstream",
            return_value={},
        ),
        patch(
            "tools.investigation.reporting.node.generate_report",
            return_value={"slack_message": "", "problem_md": "", "root_cause": ""},
        ),
    ):
        async for _event in astream_investigation("alert text"):
            pass

    expected = CLIBackedInvestigationAgent if cli_backed else ConnectedInvestigationAgent
    assert type(mock_run.call_args.args[0]) is expected


def test_run_investigation_forwards_agent_class_to_pipeline() -> None:
    """End-to-end: passing ``agent_class`` to the outermost
    :func:`run_investigation` MUST reach the agent constructor — proves
    the parameter threads through ``capability.run_investigation`` →
    ``agent_pipeline.run_agent_investigation`` → investigation agent.
    """
    _reset_sentinel()
    from tools.investigation.capability import run_investigation

    with _stubbed_stages():
        run_investigation(raw_alert={"alert": "test"}, agent_class=_SentinelAgent)

    assert len(_SentinelAgent.instances_constructed) == 1, (
        "agent_class did not thread from run_investigation through to the agent — "
        "a future refactor likely dropped the parameter forwarding."
    )


def test_run_connected_investigation_runs_plan_actions_before_agent() -> None:
    from tools.investigation.lifecycle import run_connected_investigation
    from tools.investigation.state_factory import make_initial_state

    calls: list[str] = []

    class _OrderAgent(ConnectedInvestigationAgent):
        def run(  # type: ignore[override]
            self,
            state: dict[str, Any],  # noqa: ARG002
            on_event: Any | None = None,  # noqa: ARG002
        ) -> dict[str, Any]:
            calls.append("investigation_agent")
            return {}

    state = make_initial_state(raw_alert="alert text")
    with (
        patch(
            "tools.investigation.stages.resolve_integrations.resolve_integrations",
            side_effect=lambda _state: (
                calls.append("resolve_integrations") or {"resolved_integrations": {}}
            ),
        ),
        patch(
            "tools.investigation.stages.intake.extract_alert",
            side_effect=lambda _state: calls.append("extract_alert") or {"is_noise": False},
        ),
        patch(
            "tools.investigation.stages.plan_evidence.plan_actions",
            side_effect=lambda _state: calls.append("plan_actions") or {},
        ),
        patch(
            "tools.investigation.stages.diagnose.diagnose",
            side_effect=lambda _state: calls.append("diagnose") or {},
        ),
        patch(
            "tools.investigation.reporting.upstream_correlation.node.node_correlate_upstream",
            return_value={},
        ),
        patch("tools.investigation.reporting.deliver", return_value={}),
        pytest.warns(DeprecationWarning, match="deprecated"),
    ):
        run_connected_investigation(state, agent_class=_OrderAgent)

    assert calls == [
        "resolve_integrations",
        "extract_alert",
        "plan_actions",
        "investigation_agent",
        "diagnose",
    ]
