"""Tests for process stats and session trace sink."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from core.agent_harness.session.persistence.jsonl_storage import JsonlSessionStorage
from platform.observability.trace.process_stats import sample_thread_snapshot
from platform.observability.trace.spans import (
    NoopSessionTraceSink,
    bind_session_trace,
    emit_span,
    emit_thread_boundary,
    get_session_trace_sink,
    is_session_trace_active,
    set_session_trace_sink,
    timed_span,
)
from surfaces.interactive_shell.session.trace_sink import JsonlSessionTraceSink


def test_sample_thread_snapshot_lists_current_thread() -> None:
    snap = sample_thread_snapshot()
    assert snap["thread_count"] >= 1
    names = {row["name"] for row in snap["threads"]}
    assert threading.current_thread().name in names
    assert "main_thread_ident" in snap


def test_emit_span_and_thread_boundary_are_free_when_noop() -> None:
    set_session_trace_sink(NoopSessionTraceSink())
    assert not is_session_trace_active()
    assert emit_span(span_kind="route", name="gather_and_answer", session_id="s") == ""
    assert emit_thread_boundary("s", name="turn_boundary", phase="turn_start") == ""
    with timed_span(span_kind="component", name="x", session_id="s") as attrs:
        attrs["ok"] = True
        time.sleep(0)  # no clock work required; just ensure context works


def test_noop_emit_paths_skip_sampling_and_io(monkeypatch) -> None:
    """Production default: emit helpers must not touch process stats or sink I/O."""
    set_session_trace_sink(NoopSessionTraceSink())
    calls: list[str] = []

    def _boom(*_a, **_k):
        calls.append("sample")
        raise AssertionError("process sampling must not run on noop sink")

    monkeypatch.setattr(
        "platform.observability.trace.spans.sample_turn_boundary_stats",
        _boom,
    )
    assert emit_thread_boundary("s", name="turn_boundary", phase="turn_start") == ""
    assert emit_span(span_kind="route", name="x", session_id="s") == ""
    with timed_span(span_kind="component", name="y", session_id="s"):
        pass
    assert calls == []


def test_emit_span_writes_route_when_sink_active(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_storage.session_path",
        lambda session_id: tmp_path / f"{session_id}.jsonl",
    )
    storage = JsonlSessionStorage()
    session_id = "sess-route-test"
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_sink(JsonlSessionTraceSink(storage=storage))
    assert is_session_trace_active()
    with bind_session_trace(session_id):
        emit_span(
            span_kind="route",
            name="gather_and_answer",
            attributes={"handled": False},
        )
        with timed_span(span_kind="stage", name="extract_alert") as attrs:
            attrs["fields_updated"] = ["alert"]
            time.sleep(0.001)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()]
    kinds = {(rec["span_kind"], rec["name"]) for rec in lines if rec.get("type") == "trace_span"}
    assert ("route", "gather_and_answer") in kinds
    assert ("stage", "extract_alert") in kinds
    stage = next(r for r in lines if r.get("name") == "extract_alert")
    assert stage["duration_ms"] >= 0
    assert stage["attributes"]["fields_updated"] == ["alert"]
    set_session_trace_sink(NoopSessionTraceSink())


def test_timed_span_honors_status_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_storage.session_path",
        lambda session_id: tmp_path / f"{session_id}.jsonl",
    )
    storage = JsonlSessionStorage()
    session_id = "sess-status"
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_sink(JsonlSessionTraceSink(storage=storage))
    with timed_span(span_kind="component", name="investigation", session_id=session_id) as attrs:
        attrs["_status"] = "error"
        attrs["failure_category"] = "user_cancelled"
    rec = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["status"] == "error"
    assert "_status" not in rec.get("attributes", {})
    assert rec["attributes"]["failure_category"] == "user_cancelled"
    set_session_trace_sink(NoopSessionTraceSink())


def test_semantic_helpers_match_span_kinds(tmp_path: Path, monkeypatch) -> None:
    from platform.observability.trace.spans import (
        component_span,
        emit_route,
        llm_span,
        mark_span_outcome,
        stage_span,
        tool_span,
        traced_session,
    )

    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_storage.session_path",
        lambda session_id: tmp_path / f"{session_id}.jsonl",
    )
    storage = JsonlSessionStorage()
    session_id = "sess-helpers"
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_sink(JsonlSessionTraceSink(storage=storage))
    with traced_session(session_id, component="gateway_turn") as attrs:
        mark_span_outcome(attrs, "ok")
        emit_route("gather_and_answer", attributes={"handled": False})
        with component_span("action_turn"):
            pass
        with stage_span("intake"):
            pass
        with tool_span("echo", tool_call_id="c1") as tool_attrs:
            mark_span_outcome(tool_attrs, "ok", source="agent")
        with llm_span("model-x", iteration=1):
            pass
    kinds = {
        (rec["span_kind"], rec["name"])
        for rec in (
            json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()
        )
        if rec.get("type") == "trace_span"
    }
    assert ("component", "gateway_turn") in kinds
    assert ("route", "gather_and_answer") in kinds
    assert ("component", "action_turn") in kinds
    assert ("stage", "intake") in kinds
    assert ("tool", "echo") in kinds
    assert ("llm", "model-x") in kinds
    set_session_trace_sink(NoopSessionTraceSink())


def test_jsonl_trace_sink_writes_trace_span(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_storage.session_path",
        lambda session_id: tmp_path / f"{session_id}.jsonl",
    )
    storage = JsonlSessionStorage()
    session_id = "sess-thread-test"
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    sink = JsonlSessionTraceSink(storage=storage)
    set_session_trace_sink(sink)
    emit_thread_boundary(session_id, name="turn_boundary", phase="turn_start")
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[1])
    assert rec["type"] == "trace_span"
    assert rec["span_kind"] == "thread"
    attrs: dict[str, Any] = rec["attributes"]
    assert attrs["phase"] == "turn_start"
    assert attrs["thread_count"] >= 1
    assert isinstance(attrs["threads"], list)
    set_session_trace_sink(NoopSessionTraceSink())
    assert isinstance(get_session_trace_sink(), NoopSessionTraceSink)
