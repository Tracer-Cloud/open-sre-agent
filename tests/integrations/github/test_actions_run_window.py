"""Run listings carry a time window with a default, and admit a partial page.

Asked "what's the current error rate on github?", the agent had no tool that
understood a time window, so it hand-built a `gh api` query and chose the window
itself. One run it picked the last hour, found no completed runs, and answered
"N/A" while three goal turns went unused.

The window now has a default in code. Coverage is the other half: a listing is
one page, so a window can hold more runs than were fetched, and a rate over a
partial page is the "0% from one run" failure in a new costume.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from integrations.github.tools.actions import (
    NO_RUN_WINDOW,
    RATE_WINDOW_HOURS,
    runs_within_window,
)

_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _run(hours_ago: float, name: str = "ci") -> dict[str, object]:
    started = _NOW - timedelta(hours=hours_ago)
    return {"name": name, "created_at": started.isoformat().replace("+00:00", "Z")}


def test_the_rate_window_is_a_day_not_an_hour() -> None:
    """A rate question asks for 24 hours, not whatever the model picks."""
    assert RATE_WINDOW_HOURS == 24


def test_the_tool_default_keeps_every_run_so_forensics_still_sees_old_ones() -> None:
    """Defaulting to a window would hide the run before an incident.

    This listing also answers "which deploy failed right before the incident".
    A default window silently dropped a three-month-old fixture run in
    ``tests/tools/test_github_actions_tool.py`` — the same way it would drop the
    run an investigation was looking for.
    """
    assert NO_RUN_WINDOW == 0
    old = [_run(2000, "months-ago")]
    inside, covered = runs_within_window(old, NO_RUN_WINDOW, now=_NOW)
    assert [run["name"] for run in inside] == ["months-ago"]
    assert covered is True


def test_runs_older_than_the_window_are_dropped() -> None:
    # Arrange
    runs = [_run(1, "recent"), _run(5, "today"), _run(30, "yesterday")]

    # Act
    inside, _covered = runs_within_window(runs, 24, now=_NOW)

    # Assert
    assert [run["name"] for run in inside] == ["recent", "today"]


def test_a_page_reaching_past_the_window_counts_as_covered() -> None:
    # Arrange: an older run came back, so the page spans the whole window.
    runs = [_run(1), _run(30)]

    # Act
    _inside, covered = runs_within_window(runs, 24, now=_NOW)

    # Assert
    assert covered is True


def test_a_page_that_ends_inside_the_window_is_not_covered() -> None:
    # Arrange: every run fetched is recent, so older ones may exist unfetched.
    runs = [_run(1), _run(2), _run(3)]

    # Act
    inside, covered = runs_within_window(runs, 24, now=_NOW)

    # Assert: the caller must not report a rate over this.
    assert len(inside) == 3
    assert covered is False


def test_a_zero_window_keeps_every_run() -> None:
    # Arrange / Act
    runs = [_run(1), _run(500)]
    inside, covered = runs_within_window(runs, 0, now=_NOW)

    # Assert: the escape hatch returns the page untouched.
    assert inside == runs
    assert covered is True


def test_a_run_without_a_usable_timestamp_is_not_counted() -> None:
    # Arrange: a malformed created_at must not silently pass as in-window.
    runs = [_run(1, "good"), {"name": "broken", "created_at": "not-a-date"}, {"name": "empty"}]

    # Act
    inside, _covered = runs_within_window(runs, 24, now=_NOW)

    # Assert
    assert [run["name"] for run in inside] == ["good"]
