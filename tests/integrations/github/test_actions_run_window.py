"""Run listings can be narrowed to a window, and say what the narrowing cost.

Asked "what's the current error rate on github?", the agent had no tool that
understood a time window, so it built its own query and chose the window each
turn. One run it picked the last hour, found no completed runs, and answered
"N/A" with three goal turns unused.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from integrations.github.tools.actions import (
    NO_RUN_WINDOW,
    RATE_WINDOW_HOURS,
    window_runs,
)

_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _run(hours_ago: float, name: str = "ci") -> dict[str, object]:
    started = _NOW - timedelta(hours=hours_ago)
    return {"name": name, "created_at": started.isoformat().replace("+00:00", "Z")}


def test_the_rate_window_is_a_day_not_an_hour() -> None:
    assert RATE_WINDOW_HOURS == 24


def test_the_tool_default_is_no_window_so_forensics_still_sees_old_runs() -> None:
    """Defaulting to a window would hide the run before an incident.

    This listing also answers "which deploy failed right before the incident".
    A 24-hour default dropped a three-month-old fixture run in
    ``tests/tools/test_github_actions_tool.py`` — the same way it would drop the
    run an investigation was looking for.
    """
    assert NO_RUN_WINDOW == 0


def test_runs_older_than_the_window_are_dropped() -> None:
    # Arrange
    runs = [_run(1, "recent"), _run(5, "today"), _run(30, "yesterday")]

    # Act
    windowed = window_runs(runs, window_hours=24, now=_NOW)

    # Assert
    assert [run["name"] for run in windowed.runs] == ["recent", "today"]


def test_a_page_reaching_past_the_window_is_fully_fetched() -> None:
    # Arrange: an older run came back, so the page spans the whole window.
    # Act
    windowed = window_runs([_run(1), _run(30)], window_hours=24, now=_NOW)

    # Assert
    assert windowed.window_fully_fetched is True


def test_a_page_that_ends_inside_the_window_is_not_fully_fetched() -> None:
    # Arrange: every run fetched is recent, so older ones may exist unfetched.
    # Act
    windowed = window_runs([_run(1), _run(2), _run(3)], window_hours=24, now=_NOW)

    # Assert: a count over this is a floor, not a total.
    assert len(windowed.runs) == 3
    assert windowed.window_fully_fetched is False


def test_undated_runs_are_counted_rather_than_vanishing() -> None:
    # Arrange: a malformed created_at cannot be placed in or out of the window.
    runs = [
        _run(1, "good"),
        {"name": "broken", "created_at": "not-a-date"},
        {"name": "missing"},
    ]

    # Act
    windowed = window_runs(runs, window_hours=24, now=_NOW)

    # Assert: dropped from the count, but the caller can see how many.
    assert [run["name"] for run in windowed.runs] == ["good"]
    assert windowed.undated == 2


def test_undated_runs_are_not_evidence_that_the_page_spanned_the_window() -> None:
    # Arrange / Act: only a dated older run proves coverage.
    windowed = window_runs([{"name": "broken", "created_at": ""}], window_hours=24, now=_NOW)

    # Assert
    assert windowed.window_fully_fetched is False
    assert windowed.undated == 1
