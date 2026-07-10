from __future__ import annotations

from typing import Any

import pytest

from platform.analytics.investigation_loop import (
    begin_investigation_loop_metrics_scope,
    bound_loop_metrics,
    reset_investigation_loop_metrics,
)
from tools.investigation.capability import astream_investigation
from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent


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
async def test_astream_failure_binds_loop_metrics_on_consumer_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    scope_token = begin_investigation_loop_metrics_scope()
    try:
        with pytest.raises(RuntimeError, match="boom"):
            async for _event in astream_investigation("alert text"):
                pass
        assert bound_loop_metrics() == (5, 20)
    finally:
        reset_investigation_loop_metrics(scope_token)
