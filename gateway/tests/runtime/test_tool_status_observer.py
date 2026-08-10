"""The observer that turns runtime tool events into chat timeline rows.

A row is opened on ``tool_start`` and closed on ``tool_end``. Before this the
gateway never observed ``tool_end`` at all, so every row closed as a green
check — including on the turn that timed out having posted nothing.
"""

from __future__ import annotations

from typing import Any

from config.constants import DETACHED_LAUNCH_ROW_TITLE
from gateway.core.investigations.launch_record import (
    DetachedLaunchRecord,
    bound_detached_launch_record,
)
from gateway.core.runtime.session_agents import _ToolStatusObserver

#: A real success payload: several tools set ``error`` to ``None`` on the happy
#: path. Presence-based classification reads that as a failure; the threaded
#: ``is_error`` flag does not.
_SUCCESS_WITH_NULL_ERROR: dict[str, Any] = {"lines": ["all good"], "total": 1, "error": None}


class _RecordingSink:
    """Records the timeline calls a real transport sink would receive."""

    def __init__(self) -> None:
        self.started: list[tuple[str, str | None]] = []
        self.ended: list[tuple[bool, str | None]] = []
        self.left_open: list[tuple[str | None, str | None]] = []

    def set_tool_status(self, status: str, *, call_id: str | None = None) -> None:
        self.started.append((status, call_id))

    def end_tool_status(self, *, failed: bool, call_id: str | None = None) -> None:
        self.ended.append((failed, call_id))

    def leave_tool_status_open(
        self, *, call_id: str | None = None, title: str | None = None
    ) -> None:
        self.left_open.append((call_id, title))


def _observe(kind: str, data: dict[str, Any]) -> _RecordingSink:
    sink = _RecordingSink()
    _ToolStatusObserver(sink)(kind, data)  # type: ignore[arg-type]
    return sink


def test_a_failed_tool_closes_its_row_as_failed() -> None:
    sink = _observe(
        "tool_end",
        {"id": "call-789", "name": "kubernetes_get_pod_logs", "is_error": True},
    )

    assert sink.ended == [(True, "call-789")]


def test_a_success_payload_carrying_error_none_is_not_reported_as_failed() -> None:
    """The regression guard for re-deriving the outcome from the result dict.

    ``core.events.tool_result_is_error`` is key-*presence* based, and 38 success
    payloads across the codebase carry ``"error": None``. Anyone who "simplifies"
    the observer to read the result instead of the threaded ``is_error`` paints
    every one of those rows red, and this test is what stops them.
    """
    sink = _observe(
        "tool_end",
        {
            "id": "call-123",
            "name": "kubernetes_get_pod_logs",
            "output": _SUCCESS_WITH_NULL_ERROR,
            "is_error": False,
        },
    )

    assert sink.ended == [(False, "call-123")]


def test_a_duplicate_the_runtime_declined_is_not_a_failed_row() -> None:
    """The guard blocks a repeat by returning an error — to the model, not the reader.

    ``core.execution`` marks a call blocked by ``before_tool_call`` as
    ``is_error`` so the provider is told to stop asking. Nothing failed: the
    first call's row already carries the result. Reading ``is_error`` alone
    puts a ✗ beside the guard doing its job.
    """
    sink = _observe(
        "tool_end",
        {
            "id": "call-456",
            "name": "kubernetes_get_pod_logs",
            "is_error": True,
            "suppressed_duplicate": True,
        },
    )

    assert sink.ended == [(False, "call-456")]


def test_a_row_is_opened_against_the_call_it_belongs_to() -> None:
    sink = _observe(
        "tool_start",
        {"id": "call-1", "name": "kubernetes_get_pod_logs", "input": {"pod_name": "api"}},
    )

    assert len(sink.started) == 1
    status, call_id = sink.started[0]
    assert call_id == "call-1"
    assert "api" in status


def test_the_handoff_pseudo_tool_gets_no_row_at_either_end() -> None:
    """It is an internal control signal, not work the reader asked for."""
    assert _observe("tool_start", {"id": "h", "name": "assistant_handoff"}).started == []
    assert _observe("tool_end", {"id": "h", "name": "assistant_handoff"}).ended == []


def test_only_launch_tools_are_left_open() -> None:
    """The exact incident batch: a launch tool alongside an ordinary one.

    A whole batch emits every ``tool_start`` before any ``tool_end``
    (``core/agent/react_loop.py``), so filtering by call *count* rather than
    tool name would wrongly leave the sibling ``rootly_incidents`` row open too.
    """
    sink = _RecordingSink()
    observer = _ToolStatusObserver(sink)  # type: ignore[arg-type]
    record = DetachedLaunchRecord()
    record.note_accepted("inv-1", call_id="call-2")

    with bound_detached_launch_record(record):
        observer("tool_start", {"id": "call-1", "name": "rootly_incidents", "input": {}})
        observer("tool_start", {"id": "call-2", "name": "investigation_start", "input": {}})
        observer("tool_end", {"id": "call-1", "name": "rootly_incidents", "is_error": False})
        observer("tool_end", {"id": "call-2", "name": "investigation_start", "is_error": False})

    assert sink.ended == [(False, "call-1")]
    assert [call_id for call_id, _ in sink.left_open] == ["call-2"]


def test_a_non_detaching_slash_command_closes_normally_beside_a_detaching_one() -> None:
    """The live-repro batch: two calls both named tools in ``DETACHED_LAUNCH_TOOL_NAMES``.

    ``slash_invoke`` is in that tuple because ``/investigate`` can detach
    through it, but ``slash_invoke`` is also the one tool for every *other*
    slash command. A batch of ``slash_invoke(/integrations list)`` (completes
    normally) plus ``investigation_start`` (detaches) reproduced live as
    ``ended: []``, ``left: ['c1', 'c2']`` — scoping the old guard on
    ``record.any_accepted`` left the already-finished ``slash_invoke`` call
    spinning forever alongside the one that actually detached. Only the call
    that triggered the launch (``call_id="c2"``, matching ``note_accepted``
    below) may be left open.
    """
    sink = _RecordingSink()
    observer = _ToolStatusObserver(sink)  # type: ignore[arg-type]
    record = DetachedLaunchRecord()
    record.note_accepted("inv-1", call_id="c2")

    with bound_detached_launch_record(record):
        observer("tool_start", {"id": "c1", "name": "slash_invoke", "input": {}})
        observer("tool_start", {"id": "c2", "name": "investigation_start", "input": {}})
        observer("tool_end", {"id": "c1", "name": "slash_invoke", "is_error": False})
        observer("tool_end", {"id": "c2", "name": "investigation_start", "is_error": False})

    assert sink.ended == [(False, "c1")]
    assert [call_id for call_id, _ in sink.left_open] == ["c2"]


def test_the_left_open_row_is_retitled_to_say_it_was_handed_off() -> None:
    """A row left ``in_progress`` past the stream's end reads as stale, not active.

    Slack has no status value for "handed off" — the row is retitled once so
    the text at least says what happened, since the icon cannot.
    """
    sink = _RecordingSink()
    observer = _ToolStatusObserver(sink)  # type: ignore[arg-type]
    record = DetachedLaunchRecord()
    record.note_accepted("inv-1", call_id="call-1")

    with bound_detached_launch_record(record):
        observer("tool_start", {"id": "call-1", "name": "investigation_start", "input": {}})
        observer("tool_end", {"id": "call-1", "name": "investigation_start", "is_error": False})

    [(call_id, title)] = sink.left_open
    assert call_id == "call-1"
    assert title == DETACHED_LAUNCH_ROW_TITLE
