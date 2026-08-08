from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.benchmarks.orcabench.execution.task_context import parse_orca_task_context


def test_parses_standard_orca_current_time_and_builds_historical_window() -> None:
    context = parse_orca_task_context(
        "# RCA\n\nYou are an expert site reliability engineer. "
        "The current time is Apr 21, 2026 at 09:00 ET.\n"
    )

    assert context.current_time.astimezone(UTC) == datetime(2026, 4, 21, 13, 0, tzinfo=UTC)
    assert context.incident_window() == {
        "_schema_version": 1,
        "since": "2026-04-21T11:00:00Z",
        "until": "2026-04-21T13:00:00Z",
        "source": "caller_override",
        "confidence": 1.0,
    }


def test_rejects_missing_current_time_instead_of_using_host_clock() -> None:
    with pytest.raises(ValueError, match="missing its standardized current time"):
        parse_orca_task_context("users are reporting site issues")
