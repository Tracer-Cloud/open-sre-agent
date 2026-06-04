"""Regression tests for ``agent_class`` threading through the pipeline.

The investigation pipeline exposes an ``agent_class`` parameter at two
public surfaces:

  - :func:`app.pipeline.runners.run_investigation`
  - :func:`app.pipeline.pipeline.run_connected_investigation`

The parameter MUST thread cleanly from the outer ``run_investigation``
all the way to where the agent is constructed, so callers (e.g. test
suites, downstream integrators) can substitute a subclass without
patching internals.

These tests pin that contract — a future refactor that drops the
parameter or stops forwarding it would fail loudly here instead of
silently using the production default.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from app.agent.investigation import ConnectedInvestigationAgent


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


def test_run_connected_investigation_uses_agent_class_when_provided() -> None:
    """The pipeline must instantiate the override class, not the default."""
    _reset_sentinel()
    from app.pipeline.pipeline import run_connected_investigation
    from app.state.factory import make_initial_state

    state = make_initial_state(raw_alert="alert text")
    # Avoid running real integration/extraction; mock them to no-ops so the
    # test focuses on the agent_class threading specifically.
    with (
        patch("app.agent.context.resolve_integrations", return_value={}),
        patch("app.agent.extract.extract_alert", return_value={"is_noise": False}),
        patch("app.correlation.node.node_correlate_upstream", return_value={}),
        patch("app.delivery.deliver", return_value={}),
    ):
        run_connected_investigation(state, agent_class=_SentinelAgent)

    assert len(_SentinelAgent.instances_constructed) == 1
    assert _SentinelAgent.instances_constructed[0].was_run is True


def test_run_connected_investigation_uses_default_agent_when_class_omitted() -> None:
    """Production behavior is unchanged: omitting ``agent_class`` constructs
    :class:`ConnectedInvestigationAgent` (the default)."""
    _reset_sentinel()
    from app.pipeline.pipeline import run_connected_investigation
    from app.state.factory import make_initial_state

    state = make_initial_state(raw_alert="alert text")
    with (
        patch("app.agent.context.resolve_integrations", return_value={}),
        patch("app.agent.extract.extract_alert", return_value={"is_noise": False}),
        patch(
            "app.agent.investigation.ConnectedInvestigationAgent.run", return_value={}
        ) as mock_run,
        patch("app.correlation.node.node_correlate_upstream", return_value={}),
        patch("app.delivery.deliver", return_value={}),
    ):
        run_connected_investigation(state)  # no agent_class kwarg

    # Sentinel was never used; the production class was.
    assert _SentinelAgent.instances_constructed == []
    assert mock_run.called


def test_run_investigation_forwards_agent_class_to_pipeline() -> None:
    """End-to-end: passing ``agent_class`` to the outermost
    :func:`run_investigation` MUST reach the agent constructor — proves
    the parameter threads through ``runners.run_investigation`` →
    ``pipeline.run_connected_investigation`` → ``ConnectedInvestigationAgent``.
    """
    _reset_sentinel()
    from app.pipeline.runners import run_investigation

    with (
        patch("app.agent.context.resolve_integrations", return_value={}),
        patch("app.agent.extract.extract_alert", return_value={"is_noise": False}),
        patch("app.correlation.node.node_correlate_upstream", return_value={}),
        patch("app.delivery.deliver", return_value={}),
    ):
        run_investigation(raw_alert={"alert": "test"}, agent_class=_SentinelAgent)

    assert len(_SentinelAgent.instances_constructed) == 1, (
        "agent_class did not thread from run_investigation through to the agent — "
        "a future refactor likely dropped the parameter forwarding."
    )
