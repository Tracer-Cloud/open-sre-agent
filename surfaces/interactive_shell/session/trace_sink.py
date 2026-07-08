"""JSONL-backed :class:`~platform.observability.session_trace.SessionTraceSink` for the REPL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent_harness.session.persistence.jsonl_storage import JsonlSessionStorage
from platform.observability.session_trace import NoopSessionTraceSink, SessionTraceSink


@dataclass
class JsonlSessionTraceSink:
    """Write ``trace_span`` records through the session's JSONL storage backend."""

    storage: JsonlSessionStorage

    def emit(
        self,
        session_id: str,
        *,
        span_kind: str,
        name: str,
        status: str = "ok",
        duration_ms: int | None = None,
        attributes: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> str:
        return self.storage.append_trace_span(
            session_id,
            span_kind=span_kind,
            name=name,
            status=status,
            duration_ms=duration_ms,
            attributes=attributes,
            parent_id=parent_id,
        )


def jsonl_trace_sink_for_session(session: Any) -> SessionTraceSink:
    """Return a JSONL sink wired to ``session.storage``, or a Noop sink for
    non-JSONL (e.g. in-memory) sessions so tests don't leak trace files to disk."""
    storage = getattr(session, "storage", None)
    if not isinstance(storage, JsonlSessionStorage):
        return NoopSessionTraceSink()
    return JsonlSessionTraceSink(storage=storage)


__all__ = ["JsonlSessionTraceSink", "jsonl_trace_sink_for_session"]
