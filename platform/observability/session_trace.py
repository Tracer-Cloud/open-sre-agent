"""Session trace span port — product instrumentation for JSONL / ATM."""

from __future__ import annotations

from typing import Any, Protocol

from platform.observability.process_stats import sample_turn_boundary_stats


class SessionTraceSink(Protocol):
    """Append-only session trace spans (routes, stages, threads, resources)."""

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
        """Persist one span; return entry id (empty when persistence is unavailable)."""


class NoopSessionTraceSink:
    """Default sink before a surface registers a JSONL adapter."""

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
        del session_id, span_kind, name, status, duration_ms, attributes, parent_id
        return ""


_sink: SessionTraceSink = NoopSessionTraceSink()


def get_session_trace_sink() -> SessionTraceSink:
    return _sink


def set_session_trace_sink(sink: SessionTraceSink | None) -> None:
    global _sink
    _sink = sink if sink is not None else NoopSessionTraceSink()


def emit_thread_boundary(
    session_id: str,
    *,
    name: str,
    phase: str,
    asyncio_tasks: int | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Emit a ``span_kind=thread`` snapshot at a REPL turn or session boundary."""
    attributes = sample_turn_boundary_stats(asyncio_tasks=asyncio_tasks)
    attributes["phase"] = phase
    if extra:
        attributes.update(extra)
    return get_session_trace_sink().emit(
        session_id,
        span_kind="thread",
        name=name,
        attributes=attributes,
    )


__all__ = [
    "NoopSessionTraceSink",
    "SessionTraceSink",
    "emit_thread_boundary",
    "get_session_trace_sink",
    "set_session_trace_sink",
]
