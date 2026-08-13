"""Session-scoped gather unreachable marks (tools + sources).

``SourceCircuitBreaker`` is per gather run. Each SessionGoal continuation
starts a new gather, so without a session carry the model re-pays connect
timeouts on the same dead Grafana/Kubernetes hosts on the next turn.

Stores are on ``SessionCore.gather_unreachable_{tools,sources}`` for the
process lifetime of that session — enough for ``run_until_session_goal``
iterations. Cleared on ``/new`` / ``/resume`` via ``SessionCore.clear()``.
"""

from __future__ import annotations

from typing import Any

_TOOLS_ATTR = "gather_unreachable_tools"
_SOURCES_ATTR = "gather_unreachable_sources"


def load_gather_unreachable(session: Any) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(tools, sources)`` maps of name → reason summary."""
    tools = getattr(session, _TOOLS_ATTR, None)
    sources = getattr(session, _SOURCES_ATTR, None)
    tool_map = dict(tools) if isinstance(tools, dict) else {}
    source_map = dict(sources) if isinstance(sources, dict) else {}
    return tool_map, source_map


def store_gather_unreachable(
    session: Any,
    *,
    tools: dict[str, str],
    sources: dict[str, str],
) -> None:
    """Reconcile ``session`` with the latest breaker snapshot.

    ``tools``/``sources`` is the breaker's full current down-set, not a
    delta: a name absent from it recovered (the breaker pops a name on
    success before snapshotting), so it is dropped from the session-level
    map rather than kept forever. A name still present keeps its original
    reason — first reason wins — rather than being overwritten by the new
    snapshot's summary for the same name.
    """
    tool_map, source_map = load_gather_unreachable(session)
    new_tool_map = {
        str(name): tool_map.get(str(name), str(summary)) for name, summary in tools.items()
    }
    new_source_map = {
        str(name): source_map.get(str(name), str(summary)) for name, summary in sources.items()
    }
    # Prefer mutating existing maps when SessionCore fields are present so
    # ``/new`` ``clear()`` and live references stay consistent.
    existing_tools = getattr(session, _TOOLS_ATTR, None)
    existing_sources = getattr(session, _SOURCES_ATTR, None)
    if isinstance(existing_tools, dict):
        existing_tools.clear()
        existing_tools.update(new_tool_map)
    else:
        setattr(session, _TOOLS_ATTR, new_tool_map)
    if isinstance(existing_sources, dict):
        existing_sources.clear()
        existing_sources.update(new_source_map)
    else:
        setattr(session, _SOURCES_ATTR, new_source_map)


def clear_gather_unreachable(session: Any) -> None:
    """Drop session carry (tests / explicit recovery)."""
    existing_tools = getattr(session, _TOOLS_ATTR, None)
    existing_sources = getattr(session, _SOURCES_ATTR, None)
    if isinstance(existing_tools, dict):
        existing_tools.clear()
    else:
        setattr(session, _TOOLS_ATTR, {})
    if isinstance(existing_sources, dict):
        existing_sources.clear()
    else:
        setattr(session, _SOURCES_ATTR, {})


__all__ = [
    "clear_gather_unreachable",
    "load_gather_unreachable",
    "store_gather_unreachable",
]
