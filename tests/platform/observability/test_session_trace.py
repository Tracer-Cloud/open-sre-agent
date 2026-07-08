"""Tests for process stats and session trace sink."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from core.agent_harness.session.persistence.jsonl_storage import JsonlSessionStorage
from platform.observability.process_stats import sample_thread_snapshot
from platform.observability.session_trace import (
    NoopSessionTraceSink,
    emit_thread_boundary,
    get_session_trace_sink,
    set_session_trace_sink,
)
from surfaces.interactive_shell.session.trace_sink import JsonlSessionTraceSink


def test_sample_thread_snapshot_lists_current_thread() -> None:
    snap = sample_thread_snapshot()
    assert snap["thread_count"] >= 1
    names = {row["name"] for row in snap["threads"]}
    assert threading.current_thread().name in names
    assert "main_thread_ident" in snap


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
    attrs = rec["attributes"]
    assert attrs["phase"] == "turn_start"
    assert attrs["thread_count"] >= 1
    assert isinstance(attrs["threads"], list)
    set_session_trace_sink(NoopSessionTraceSink())
    assert isinstance(get_session_trace_sink(), NoopSessionTraceSink)
