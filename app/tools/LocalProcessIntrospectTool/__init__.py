"""Introspect a local agent process by PID for in-shell incident response.

Phase 5 of the monitor-local-agents roadmap. The OpenSRE investigation
pipeline is a planner that picks tools to diagnose problems; to run it
against a stuck *local* agent (Claude Code, Cursor, Aider, Codex, Gemini, …)
it needs a tool that can look inside a process. This wraps the per-PID probe
(:mod:`app.agents.probe`) and the on-disk stdout tail
(:func:`app.agents.tail.read_tail_lines`) as a first-class tool the planner
can call during an investigation surfaced in the interactive shell.

Not directly user-visible: it appears as a tool call on the diagnosis card
when the Phase 5 SLO watchdog opens an investigation against a stuck agent.
"""

from __future__ import annotations

from typing import Any

from app.agents.probe import ProcessSnapshot, probe
from app.agents.tail import AttachUnsupported, read_tail_lines
from app.tools.tool_decorator import tool

# Acceptance criterion: return the last 50 stdout lines alongside the snapshot.
_MAX_STDOUT_LINES = 50


def _snapshot_to_dict(snapshot: ProcessSnapshot) -> dict[str, Any]:
    """Render a :class:`ProcessSnapshot` as JSON-safe primitives.

    ``datetime`` fields become ISO-8601 strings; the ``None``-able resource
    fields (POSIX-only fds, privileged connection count) pass through as-is.
    """
    return {
        "pid": snapshot.pid,
        "cpu_percent": snapshot.cpu_percent,
        "rss_mb": snapshot.rss_mb,
        "num_fds": snapshot.num_fds,
        "num_connections": snapshot.num_connections,
        "status": snapshot.status,
        "started_at": snapshot.started_at.isoformat(),
        "last_output_at": (
            snapshot.last_output_at.isoformat() if snapshot.last_output_at is not None else None
        ),
    }


@tool(
    name="local_process_introspect",
    source="knowledge",
    description=(
        "Introspect a local agent process by its PID. Returns a psutil "
        "resource snapshot (CPU%, resident memory, open file descriptors, "
        "connection count, status, start time, last-output time) plus the "
        f"last {_MAX_STDOUT_LINES} lines of the process's stdout. Use this to "
        "diagnose a stuck, looping, or resource-hungry local agent (Claude "
        "Code, Cursor, Aider, Codex, Gemini, …) during an in-shell incident."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pid": {
                "type": "integer",
                "description": "Process id of the local agent to introspect.",
            },
        },
        "required": ["pid"],
    },
    surfaces=("investigation",),
    use_cases=[
        "inspect a stuck local agent's CPU and memory usage during an incident",
        "read a local agent's recent stdout to find an error or stall point",
        "confirm whether a local agent process is still alive and making progress",
    ],
    outputs={
        "found": "True when the pid maps to a process we could probe",
        "snapshot": "psutil resource snapshot, or null when the pid is gone",
        "stdout_tail": f"List of up to the last {_MAX_STDOUT_LINES} stdout lines",
        "stdout_available": "True when the stdout tail could be read",
        "stdout_error": "Reason the stdout tail was unavailable, when applicable",
    },
)
def local_process_introspect(pid: int) -> dict[str, Any]:
    """Return a resource snapshot and recent stdout for a local process.

    The snapshot and the stdout tail fail independently: a process can be
    alive and probeable while its stdout is untailable (a TTY, a pipe, or a
    cross-user fd), so the stdout failure is reported in ``stdout_error``
    rather than failing the whole call.
    """
    snapshot = probe(pid)
    result: dict[str, Any] = {
        "source": "knowledge",
        "pid": pid,
        "found": snapshot is not None,
        "snapshot": _snapshot_to_dict(snapshot) if snapshot is not None else None,
        "stdout_tail": [],
        "stdout_available": False,
        "stdout_error": None,
    }
    try:
        result["stdout_tail"] = read_tail_lines(pid, max_lines=_MAX_STDOUT_LINES)
        result["stdout_available"] = True
    except AttachUnsupported as exc:
        result["stdout_error"] = exc.reason
    return result


__all__ = ["local_process_introspect"]
