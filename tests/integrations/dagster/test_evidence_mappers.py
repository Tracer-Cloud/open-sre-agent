"""Evidence mapper tests for the Dagster tools."""

import pytest

from tools.investigation.stages.gather_evidence.tools import merge_tool_evidence


def _entry_for(tool_name: str, output: dict[str, object]) -> dict[str, object]:
    evidence: dict[str, object] = {}

    merge_tool_evidence(evidence, tool_name, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, dict)
    return entry


@pytest.mark.parametrize(
    ("tool_name", "output"),
    [
        (
            "list_dagster_assets",
            {
                "data": {
                    "assetsOrError": {
                        "__typename": "AssetConnection",
                        "nodes": [],
                    }
                }
            },
        ),
        (
            "get_dagster_run_logs",
            {
                "data": {
                    "logsForRun": {
                        "__typename": "EventConnection",
                        "events": [],
                    }
                },
                "summary": {"failure_count": 0},
            },
        ),
        (
            "list_dagster_runs",
            {
                "data": {
                    "runsOrError": {
                        "__typename": "Runs",
                        "results": [],
                    }
                }
            },
        ),
        (
            "list_dagster_schedule_ticks",
            {
                "data": {
                    "scheduleOrError": {
                        "__typename": "Schedule",
                        "scheduleState": {"ticks": []},
                    }
                }
            },
        ),
        (
            "list_dagster_sensor_ticks",
            {
                "data": {
                    "sensorOrError": {
                        "__typename": "Sensor",
                        "sensorState": {"ticks": []},
                    }
                }
            },
        ),
    ],
)
def test_dagster_evidence_mappers_skip_empty_outputs(
    tool_name: str, output: dict[str, object]
) -> None:
    evidence: dict[str, object] = {}

    merge_tool_evidence(evidence, tool_name, output, {})

    assert "catalog_entries" not in evidence


def test_list_dagster_assets_records_assets_as_evidence() -> None:
    entry = _entry_for(
        "list_dagster_assets",
        {
            "data": {
                "assetsOrError": {
                    "__typename": "AssetConnection",
                    "nodes": [{"key": {"path": ["orders"]}}, {"key": {"path": ["users"]}}],
                }
            }
        },
    )

    assert entry["source"] == "list_dagster_assets"
    assert entry["summary"] == "2 assets"


def test_get_dagster_run_logs_records_events_and_failures_as_evidence() -> None:
    entry = _entry_for(
        "get_dagster_run_logs",
        {
            "data": {
                "logsForRun": {
                    "__typename": "EventConnection",
                    "events": [{"message": "started"}, {"message": "failed"}],
                }
            },
            "summary": {"failure_count": 1},
        },
    )

    assert entry["source"] == "get_dagster_run_logs"
    assert entry["summary"] == "2 events, 1 step failure"


@pytest.mark.parametrize(
    "summary",
    [
        {"failure_count": 1, "truncated": True},
        {"failure_count": 1, "fetch_error": "page 2 unavailable"},
    ],
)
def test_get_dagster_run_logs_marks_partial_results_as_lower_bound(
    summary: dict[str, object],
) -> None:
    entry = _entry_for(
        "get_dagster_run_logs",
        {
            "data": {
                "logsForRun": {
                    "__typename": "EventConnection",
                    "events": [{"message": "started"}, {"message": "failed"}],
                }
            },
            "summary": summary,
        },
    )

    assert (
        entry["summary"]
        == "2 events, 1 step failure (partial results; failure count is a lower bound)"
    )


def test_list_dagster_runs_records_runs_as_evidence() -> None:
    entry = _entry_for(
        "list_dagster_runs",
        {
            "data": {
                "runsOrError": {
                    "__typename": "Runs",
                    "results": [{"runId": "run-1"}, {"runId": "run-2"}],
                }
            }
        },
    )

    assert entry["source"] == "list_dagster_runs"
    assert entry["summary"] == "2 runs"


def test_list_dagster_schedule_ticks_records_ticks_as_evidence() -> None:
    entry = _entry_for(
        "list_dagster_schedule_ticks",
        {
            "data": {
                "scheduleOrError": {
                    "__typename": "Schedule",
                    "scheduleState": {"ticks": [{"id": "tick-1"}, {"id": "tick-2"}]},
                }
            }
        },
    )

    assert entry["source"] == "list_dagster_schedule_ticks"
    assert entry["summary"] == "2 schedule ticks"


def test_list_dagster_sensor_ticks_records_ticks_as_evidence() -> None:
    entry = _entry_for(
        "list_dagster_sensor_ticks",
        {
            "data": {
                "sensorOrError": {
                    "__typename": "Sensor",
                    "sensorState": {"ticks": [{"id": "tick-1"}, {"id": "tick-2"}]},
                }
            }
        },
    )

    assert entry["source"] == "list_dagster_sensor_ticks"
    assert entry["summary"] == "2 sensor ticks"
