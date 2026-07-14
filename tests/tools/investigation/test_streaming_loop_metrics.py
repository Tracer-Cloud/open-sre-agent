from __future__ import annotations

from typing import Any

import pytest

from platform.analytics.investigation_loop import (
    begin_investigation_loop_metrics_scope,
    bound_loop_metrics,
    publish_loop_metrics_from_stream_failure,
    reset_investigation_loop_metrics,
)
from tools.investigation.capability import astream_investigation
from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent
from tools.investigation.streaming import InvestigationPipelineStreamError


def _agent_run_stub(
    _self: ConnectedInvestigationAgent,
    _state: dict[str, Any],
    on_event: Any | None = None,
) -> dict[str, Any]:
    if on_event is not None:
        on_event("agent_start", {})
        on_event("agent_end", {"investigation_loop_count": 5})
    return {"investigation_loop_count": 5, "investigation_iteration_cap": 20}


@pytest.mark.anyio
async def test_astream_failure_propagates_wrapped_stream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The async consumer must not unwrap before the main-thread bridge runs."""
    monkeypatch.setattr(
        "tools.investigation.stages.resolve_integrations.resolve_integrations",
        lambda _state: {"resolved_integrations": {}},
    )
    monkeypatch.setattr(
        "tools.investigation.stages.intake.extract_alert",
        lambda _state: {"alert_name": "test-alert", "is_noise": False},
    )
    monkeypatch.setattr(
        "tools.investigation.stages.plan_evidence.plan_actions",
        lambda _state: {"planned_actions": ["query_logs"]},
    )
    monkeypatch.setattr(ConnectedInvestigationAgent, "run", _agent_run_stub)
    monkeypatch.setattr(
        "tools.investigation.stages.diagnose.diagnose",
        lambda _state: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(InvestigationPipelineStreamError) as exc_info:
        async for _event in astream_investigation("alert text"):
            pass

    wrapped = exc_info.value
    assert wrapped.loop_count == 5
    assert wrapped.iteration_cap == 20
    assert isinstance(wrapped.cause, RuntimeError)


def test_main_thread_bridge_binds_metrics_from_wrapped_stream_failure() -> None:
    """Mirrors stream_investigation_cli / session_runner queue handling."""
    wrapped = InvestigationPipelineStreamError(
        cause=RuntimeError("boom"),
        loop_count=4,
        iteration_cap=20,
    )
    scope_token = begin_investigation_loop_metrics_scope()
    try:
        unwrapped = publish_loop_metrics_from_stream_failure(wrapped)
        assert isinstance(unwrapped, RuntimeError)
        assert bound_loop_metrics() == (4, 20)
    finally:
        reset_investigation_loop_metrics(scope_token)
