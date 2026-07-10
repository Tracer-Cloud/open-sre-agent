"""Lifecycle stage spans — investigation pipeline emits stage_kind spans."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from core.agent_harness.session.persistence.jsonl_storage import JsonlSessionStorage
from platform.observability.trace.spans import (
    NoopSessionTraceSink,
    bind_session_trace,
    set_session_trace_sink,
)
from surfaces.interactive_shell.session.trace_sink import JsonlSessionTraceSink
from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent


@pytest.fixture(autouse=True)
def _reset_session_trace_sink() -> Any:
    set_session_trace_sink(NoopSessionTraceSink())
    yield
    set_session_trace_sink(NoopSessionTraceSink())


class _QuietAgent(ConnectedInvestigationAgent):
    def run(  # type: ignore[override]
        self,
        state: dict[str, Any],  # noqa: ARG002
        on_event: Any | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        return {"gather_evidence_ran": True}


def test_run_connected_investigation_emits_stage_spans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each pipeline stage is timed under ``span_kind=stage`` when tracing is on."""
    from tools.investigation.lifecycle import run_connected_investigation
    from tools.investigation.state_factory import make_initial_state

    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_storage.session_path",
        lambda session_id: tmp_path / f"{session_id}.jsonl",
    )
    storage = JsonlSessionStorage()
    session_id = "sess-lifecycle-stages"
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_sink(JsonlSessionTraceSink(storage=storage))

    state = make_initial_state(raw_alert="alert text")
    with (
        bind_session_trace(session_id),
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
        run_connected_investigation(state, agent_class=_QuietAgent)

    spans = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").strip().splitlines()
        if json.loads(line).get("type") == "trace_span"
    ]
    stage_names = [rec["name"] for rec in spans if rec.get("span_kind") == "stage"]
    assert stage_names == [
        "resolve_integrations",
        "intake",
        "plan_evidence",
        "gather_evidence",
        "diagnose",
        "deliver",
    ]
    assert all(rec["status"] == "ok" for rec in spans if rec.get("span_kind") == "stage")
    assert all("duration_ms" in rec for rec in spans if rec.get("span_kind") == "stage")


def test_run_connected_investigation_skips_later_stages_on_noise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.investigation.lifecycle import run_connected_investigation
    from tools.investigation.state_factory import make_initial_state

    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_storage.session_path",
        lambda session_id: tmp_path / f"{session_id}.jsonl",
    )
    storage = JsonlSessionStorage()
    session_id = "sess-lifecycle-noise"
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_sink(JsonlSessionTraceSink(storage=storage))

    state = make_initial_state(raw_alert="noise")
    with (
        bind_session_trace(session_id),
        patch(
            "tools.investigation.stages.resolve_integrations.resolve_integrations",
            return_value={"resolved_integrations": {}},
        ),
        patch(
            "tools.investigation.stages.intake.extract_alert",
            return_value={"is_noise": True},
        ),
    ):
        run_connected_investigation(state, agent_class=_QuietAgent)

    stage_names = [
        json.loads(line)["name"]
        for line in path.read_text(encoding="utf-8").strip().splitlines()
        if json.loads(line).get("type") == "trace_span"
        and json.loads(line).get("span_kind") == "stage"
    ]
    assert stage_names == ["resolve_integrations", "intake"]


def test_run_connected_investigation_noop_sink_emits_nothing() -> None:
    """Headless / gateway default: pipeline stages must not require a sink."""
    from platform.observability.trace.spans import get_session_trace_sink
    from tools.investigation.lifecycle import run_connected_investigation
    from tools.investigation.state_factory import make_initial_state

    assert isinstance(get_session_trace_sink(), NoopSessionTraceSink)
    state = make_initial_state(raw_alert="alert text")
    with (
        patch(
            "tools.investigation.stages.resolve_integrations.resolve_integrations",
            return_value={"resolved_integrations": {}},
        ),
        patch(
            "tools.investigation.stages.intake.extract_alert",
            return_value={"is_noise": True},
        ),
    ):
        out = run_connected_investigation(state, agent_class=_QuietAgent)
    assert out.get("is_noise") is True
