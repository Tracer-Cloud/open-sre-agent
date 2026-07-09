"""Tests for process stats and session trace sink."""

from __future__ import annotations

import contextvars
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from core.agent_harness.session.persistence.jsonl_storage import JsonlSessionStorage
from platform.observability.trace import process_stats
from platform.observability.trace.process_stats import (
    sample_resource_snapshot,
    sample_thread_snapshot,
    sample_turn_boundary_stats,
)
from platform.observability.trace.spans import (
    NoopSessionTraceSink,
    bind_session_trace,
    component_span,
    current_trace_session_id,
    emit_route,
    emit_span,
    emit_thread_boundary,
    get_session_trace_sink,
    is_session_trace_active,
    llm_span,
    mark_span_outcome,
    set_session_trace_sink,
    stage_span,
    timed_span,
    tool_span,
    traced_session,
)
from surfaces.interactive_shell.session.trace_sink import (
    JsonlSessionTraceSink,
    jsonl_trace_sink_for_session,
)


@pytest.fixture(autouse=True)
def _reset_session_trace_sink() -> Any:
    set_session_trace_sink(NoopSessionTraceSink())
    yield
    set_session_trace_sink(NoopSessionTraceSink())


def _seed_session_jsonl(tmp_path: Path, session_id: str) -> Path:
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    return path


def _activate_jsonl_sink(tmp_path: Path, session_id: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_storage.session_path",
        lambda sid: tmp_path / f"{sid}.jsonl",
    )
    path = _seed_session_jsonl(tmp_path, session_id)
    set_session_trace_sink(JsonlSessionTraceSink(storage=JsonlSessionStorage()))
    return path


def _trace_spans(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").strip().splitlines()
        if json.loads(line).get("type") == "trace_span"
    ]


def test_sample_thread_snapshot_lists_current_thread() -> None:
    snap = sample_thread_snapshot()
    assert snap["thread_count"] >= 1
    names = {row["name"] for row in snap["threads"]}
    assert threading.current_thread().name in names
    assert "main_thread_ident" in snap


def test_sample_resource_snapshot_includes_gc_counts() -> None:
    snap = sample_resource_snapshot()
    assert {"gc_gen0", "gc_gen1", "gc_gen2"} <= snap.keys()
    # POSIX: rss_mb present; Windows (no ``resource``): omitted.
    if process_stats._resource is not None:
        assert "rss_mb" in snap
        assert isinstance(snap["rss_mb"], float)


def test_sample_resource_snapshot_skips_rss_without_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows / non-POSIX: ``resource`` is absent; snapshot must still succeed."""
    monkeypatch.setattr(process_stats, "_resource", None)
    snap = sample_resource_snapshot()
    assert "rss_mb" not in snap
    assert {"gc_gen0", "gc_gen1", "gc_gen2"} <= snap.keys()
    combined = sample_turn_boundary_stats()
    assert "rss_mb" not in combined
    assert "thread_count" in combined


def test_emit_span_and_thread_boundary_are_free_when_noop() -> None:
    assert not is_session_trace_active()
    assert emit_span(span_kind="route", name="gather_and_answer", session_id="s") == ""
    assert emit_thread_boundary("s", name="turn_boundary", phase="turn_start") == ""
    with timed_span(span_kind="component", name="x", session_id="s") as attrs:
        attrs["ok"] = True
        time.sleep(0)


def test_noop_emit_paths_skip_sampling_and_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production default: emit helpers must not touch process stats or sink I/O."""
    calls: list[str] = []

    def _boom(*_a: Any, **_k: Any) -> Any:
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


def test_emit_span_without_session_id_is_noop_even_when_sink_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _activate_jsonl_sink(tmp_path, "sess-no-sid", monkeypatch)
    assert is_session_trace_active()
    assert emit_span(span_kind="route", name="orphan") == ""
    assert emit_route("orphan") == ""
    with timed_span(span_kind="component", name="orphan"):
        pass
    assert _trace_spans(path) == []


def test_bind_session_trace_sets_and_clears_context() -> None:
    assert current_trace_session_id() is None
    with bind_session_trace("sess-bound"):
        assert current_trace_session_id() == "sess-bound"
    assert current_trace_session_id() is None
    with bind_session_trace(None):
        assert current_trace_session_id() is None
    with bind_session_trace(""):
        assert current_trace_session_id() is None


def test_emit_span_writes_route_when_sink_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "sess-route-test"
    path = _activate_jsonl_sink(tmp_path, session_id, monkeypatch)
    with bind_session_trace(session_id):
        emit_span(
            span_kind="route",
            name="gather_and_answer",
            attributes={"handled": False},
        )
        with timed_span(span_kind="stage", name="extract_alert") as attrs:
            attrs["fields_updated"] = ["alert"]
            time.sleep(0.001)
    kinds = {(rec["span_kind"], rec["name"]) for rec in _trace_spans(path)}
    assert ("route", "gather_and_answer") in kinds
    assert ("stage", "extract_alert") in kinds
    stage = next(r for r in _trace_spans(path) if r.get("name") == "extract_alert")
    assert stage["duration_ms"] >= 0
    assert stage["attributes"]["fields_updated"] == ["alert"]


def test_timed_span_honors_status_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from platform.observability.trace.spans import SPAN_STATUS_ATTR, SPAN_STATUS_ERROR

    session_id = "sess-status"
    path = _activate_jsonl_sink(tmp_path, session_id, monkeypatch)
    with timed_span(span_kind="component", name="investigation", session_id=session_id) as attrs:
        attrs[SPAN_STATUS_ATTR] = SPAN_STATUS_ERROR
        attrs["failure_category"] = "user_cancelled"
    rec = _trace_spans(path)[-1]
    assert rec["status"] == SPAN_STATUS_ERROR
    assert SPAN_STATUS_ATTR not in rec.get("attributes", {})
    assert rec["attributes"]["failure_category"] == "user_cancelled"


def test_timed_span_marks_error_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from platform.observability.trace.spans import SPAN_STATUS_ERROR

    def _raise_runtime_error() -> None:
        raise RuntimeError("boom")

    session_id = "sess-exc"
    path = _activate_jsonl_sink(tmp_path, session_id, monkeypatch)
    # Outer raises so the exception still propagates through timed_span's
    # finally (status=error); inner span must not be wrapped by pytest.raises
    # or the exception would be swallowed before the span exits.
    with (
        pytest.raises(RuntimeError, match="boom"),
        timed_span(span_kind="component", name="failing", session_id=session_id),
    ):
        _raise_runtime_error()
    rec = _trace_spans(path)[-1]
    assert rec["name"] == "failing"
    assert rec["status"] == SPAN_STATUS_ERROR
    assert rec["duration_ms"] >= 0


def test_timed_span_marks_error_on_base_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # KeyboardInterrupt / CancelledError derive from BaseException, not Exception.
    from platform.observability.trace.spans import SPAN_STATUS_ERROR

    def _raise_keyboard_interrupt() -> None:
        raise KeyboardInterrupt

    session_id = "sess-cancel"
    path = _activate_jsonl_sink(tmp_path, session_id, monkeypatch)
    with (
        pytest.raises(KeyboardInterrupt),
        timed_span(span_kind="tool", name="cancelled", session_id=session_id),
    ):
        _raise_keyboard_interrupt()
    rec = _trace_spans(path)[-1]
    assert rec["name"] == "cancelled"
    assert rec["status"] == SPAN_STATUS_ERROR


def test_timed_span_exception_status_beats_preset_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pre-set "ok" override must not mask an exception that propagates out.
    from platform.observability.trace.spans import (
        SPAN_STATUS_ATTR,
        SPAN_STATUS_ERROR,
        SPAN_STATUS_OK,
    )

    def _boom() -> None:
        raise RuntimeError("boom")

    session_id = "sess-override-race"
    path = _activate_jsonl_sink(tmp_path, session_id, monkeypatch)
    with (
        pytest.raises(RuntimeError, match="boom"),
        timed_span(span_kind="tool", name="racing", session_id=session_id) as attrs,
    ):
        attrs[SPAN_STATUS_ATTR] = SPAN_STATUS_OK
        _boom()
    rec = _trace_spans(path)[-1]
    assert rec["status"] == SPAN_STATUS_ERROR


def test_stage_span_emits_from_thread_with_copied_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The investigation pipeline runs stages in a worker thread; a copied context
    # carries the bound session id across the thread boundary so the span emits.
    session_id = "sess-thread"
    path = _activate_jsonl_sink(tmp_path, session_id, monkeypatch)

    def _emit_stage() -> None:
        with stage_span("gather_evidence"):
            pass

    with bind_session_trace(session_id):
        thread = threading.Thread(target=contextvars.copy_context().run, args=(_emit_stage,))
        thread.start()
        thread.join()

    assert "gather_evidence" in {rec["name"] for rec in _trace_spans(path)}


def test_stage_span_drops_in_plain_thread_without_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A plain thread starts with a fresh context, so the bound session id is unseen.
    session_id = "sess-thread-plain"
    path = _activate_jsonl_sink(tmp_path, session_id, monkeypatch)

    def _emit_stage() -> None:
        with stage_span("gather_evidence"):
            pass

    with bind_session_trace(session_id):
        thread = threading.Thread(target=_emit_stage)
        thread.start()
        thread.join()

    assert _trace_spans(path) == []


def test_mark_span_outcome_sets_status_and_extra_attrs() -> None:
    from platform.observability.trace.spans import SPAN_STATUS_ATTR, SPAN_STATUS_ERROR

    attrs: dict[str, Any] = {}
    mark_span_outcome(attrs, "ok", source="agent")
    assert attrs == {"outcome": "ok", "source": "agent"}

    attrs = {}
    mark_span_outcome(attrs, "blocked", error=True, reason="deny", ignored=None)
    assert attrs["outcome"] == "blocked"
    assert attrs[SPAN_STATUS_ATTR] == SPAN_STATUS_ERROR
    assert attrs["reason"] == "deny"
    assert "ignored" not in attrs


def test_semantic_helpers_match_span_kinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = "sess-helpers"
    path = _activate_jsonl_sink(tmp_path, session_id, monkeypatch)
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
    spans = _trace_spans(path)
    kinds = {(rec["span_kind"], rec["name"]) for rec in spans}
    assert ("component", "gateway_turn") in kinds
    assert ("route", "gather_and_answer") in kinds
    assert ("component", "action_turn") in kinds
    assert ("stage", "intake") in kinds
    assert ("tool", "echo") in kinds
    assert ("llm", "model-x") in kinds

    tool = next(r for r in spans if r["span_kind"] == "tool")
    assert tool["attributes"]["tool_call_id"] == "c1"
    assert tool["attributes"]["outcome"] == "ok"
    assert tool["attributes"]["source"] == "agent"

    llm = next(r for r in spans if r["span_kind"] == "llm")
    assert llm["attributes"]["iteration"] == 1

    gateway = next(r for r in spans if r["name"] == "gateway_turn")
    assert gateway["attributes"]["outcome"] == "ok"


def test_traced_session_noop_when_session_id_missing() -> None:
    assert not is_session_trace_active()
    with traced_session(None, component="gateway_turn") as attrs:
        attrs["note"] = "still mutable"
        assert current_trace_session_id() is None
    assert attrs["note"] == "still mutable"


def test_jsonl_trace_sink_writes_trace_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "sess-thread-test"
    path = _activate_jsonl_sink(tmp_path, session_id, monkeypatch)
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


def test_jsonl_trace_sink_for_session_uses_jsonl_or_noop() -> None:
    from core.agent_harness.session import InMemorySessionStorage
    from surfaces.interactive_shell.session import Session

    jsonl_session = Session(storage=JsonlSessionStorage())
    assert isinstance(jsonl_trace_sink_for_session(jsonl_session), JsonlSessionTraceSink)

    memory_session = Session(storage=InMemorySessionStorage())
    assert isinstance(jsonl_trace_sink_for_session(memory_session), NoopSessionTraceSink)

    assert isinstance(jsonl_trace_sink_for_session(object()), NoopSessionTraceSink)


def test_set_session_trace_sink_none_restores_noop() -> None:
    class _RecordingSink:
        def emit(self, *_a: Any, **_k: Any) -> str:
            return "id"

    set_session_trace_sink(_RecordingSink())  # type: ignore[arg-type]
    assert is_session_trace_active()
    set_session_trace_sink(None)
    assert not is_session_trace_active()
    assert isinstance(get_session_trace_sink(), NoopSessionTraceSink)
