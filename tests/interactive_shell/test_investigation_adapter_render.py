"""The foreground stream renderer must be the only painter for a streamed run."""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any

from rich.console import Console

from core.domain.stream import StreamEvent
from platform.observability.render.progress import (
    NoopProgressTracker,
    get_progress_tracker,
    silence_progress_tracker,
)
from surfaces.interactive_shell.runtime.investigation_adapter import repl_foreground_renderer


def _node_event(kind: str, node: str, data: dict[str, Any] | None = None) -> StreamEvent:
    return StreamEvent(
        event_type="events",
        data={"event": kind, "name": node, "data": data or {}},
        node_name=node,
        kind=kind,
    )


def test_foreground_render_keeps_global_tracker_silent_mid_stream() -> None:
    # Arrange: the streamed pipeline silences the global tracker at stream
    # start; pipeline stages then reach it via ``get_progress_tracker`` from a
    # background thread while the StreamRenderer paints events.
    silence_progress_tracker()
    mid_stream_trackers: list[type] = []

    def _events() -> Iterator[StreamEvent]:
        yield _node_event("on_chain_start", "extract_alert")
        # What a pipeline stage sees between events: a real tracker here means
        # two painters render every stage line (the doubled-progress failure).
        mid_stream_trackers.append(type(get_progress_tracker()))
        yield _node_event("on_chain_end", "extract_alert", {"output": {}})
        yield StreamEvent(event_type="end", data={"output": {"report": "r"}})

    console = Console(file=io.StringIO(), force_terminal=False)

    # Act
    state = repl_foreground_renderer(console)(_events())

    # Assert: stages stay on the Noop tracker for the whole stream.
    assert mid_stream_trackers == [NoopProgressTracker]
    assert isinstance(state, dict)
