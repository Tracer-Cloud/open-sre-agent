"""Tests for optional ``@traceable`` — free when session tracing is inactive."""

from __future__ import annotations

import json
from pathlib import Path

from core.agent_harness.session.persistence.jsonl_storage import JsonlSessionStorage
from platform.observability.trace.hook import traceable
from platform.observability.trace.spans import (
    NoopSessionTraceSink,
    set_session_trace_sink,
)
from surfaces.interactive_shell.session.trace_sink import JsonlSessionTraceSink


def test_traceable_is_near_free_passthrough_when_noop() -> None:
    set_session_trace_sink(NoopSessionTraceSink())

    @traceable("investigation")
    def traced_function() -> str:
        return "ok"

    assert traced_function() == "ok"
    assert traced_function.__name__ == "traced_function"
    # Wrapper exists so an active sink can emit spans, but call semantics
    # stay identical when the default noop sink is registered.
    assert callable(traced_function)


def test_traceable_preserves_args_kwargs_return_value_and_metadata() -> None:
    set_session_trace_sink(NoopSessionTraceSink())

    @traceable("span-name")
    def traced_function(value: int, *, suffix: str) -> str:
        """Original docstring."""
        return f"{value}{suffix}"

    assert traced_function(7, suffix="ms") == "7ms"
    assert traced_function.__name__ == "traced_function"
    assert traced_function.__doc__ == "Original docstring."


def test_traceable_emits_component_span_when_sink_active(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_storage.session_path",
        lambda session_id: tmp_path / f"{session_id}.jsonl",
    )
    storage = JsonlSessionStorage()
    session_id = "sess-traceable"
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_sink(JsonlSessionTraceSink(storage=storage))

    @traceable(name="investigation")
    def run_investigation() -> str:
        return "done"

    from platform.observability.trace.spans import bind_session_trace

    with bind_session_trace(session_id):
        assert run_investigation() == "done"

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()]
    kinds = {(rec["span_kind"], rec["name"]) for rec in lines if rec.get("type") == "trace_span"}
    assert ("component", "investigation") in kinds
    set_session_trace_sink(NoopSessionTraceSink())
