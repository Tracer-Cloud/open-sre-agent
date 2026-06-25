"""Synthetic RCA scenario using Dagster as the evidence source.

Validates that:
- A Dagster alert source maps to dagster tools in both seeding maps.
- Each of the 4 Dagster tool functions, when wired through a fixture
  Dagster instance, surfaces realistic GraphQL responses end-to-end.

The fixture Dagster instance is implemented as an httpx.MockTransport
returning canned JSON for each GraphQL query. This exercises the full
stack: tool function → integration helper → DagsterClient → mock transport.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.domain.alerts.alert_source import (
    ALERT_SOURCE_TO_SEED_TOOL_SOURCES as _SEEDING_MAP,
)
from app.core.domain.alerts.alert_source import (
    ALERT_SOURCE_TO_TOOL_SOURCES as _PROMPT_MAP,
)
from app.integrations import dagster as dagster_integration
from app.services.dagster import DagsterClient
from app.tools.DagsterAssetsTool import list_dagster_assets
from app.tools.DagsterRunLogsTool import get_dagster_run_logs
from app.tools.DagsterRunsTool import list_dagster_runs
from app.tools.DagsterSchedulesTool import list_dagster_schedule_ticks
from app.tools.DagsterSensorsTool import list_dagster_sensor_ticks

pytestmark = pytest.mark.synthetic


# --- fixture Dagster instance ----------------------------------------------


_FAILED_RUN_ID = "f7788e00-d8ad-40f6-aaa5-ab86037a127f"

_FIXTURE_LIST_RUNS = {
    "data": {
        "runsOrError": {
            "__typename": "Runs",
            "results": [
                {
                    "runId": _FAILED_RUN_ID,
                    "status": "FAILURE",
                    "jobName": "demo_job",
                    "startTime": 1779906297.95,
                    "endTime": 1779906299.04,
                    "creationTime": 1779906297.16,
                }
            ],
            "count": 1,
        }
    }
}

_FIXTURE_GET_RUN_LOGS = {
    "data": {
        "logsForRun": {
            "__typename": "EventConnection",
            "events": [
                {
                    "__typename": "RunStartEvent",
                    "runId": _FAILED_RUN_ID,
                    "message": 'Started execution of run for "demo_job".',
                    "timestamp": "1779906297950",
                    "level": "DEBUG",
                    "stepKey": None,
                    "eventType": "RUN_START",
                },
                {
                    "__typename": "ExecutionStepFailureEvent",
                    "runId": _FAILED_RUN_ID,
                    "message": 'Execution of step "failing_op" failed.',
                    "timestamp": "1779906298920",
                    "level": "ERROR",
                    "stepKey": "failing_op",
                    "eventType": "STEP_FAILURE",
                    "error": {
                        "message": (
                            "dagster._core.errors.DagsterExecutionStepExecutionError: "
                            'Error occurred while executing op "failing_op":'
                        ),
                        "stack": ["  File .../op_execution_error_boundary, in op..."],
                        "className": "DagsterExecutionStepExecutionError",
                        "cause": {
                            "message": "ValueError: intentional failure with hello",
                            "stack": [
                                "  File .../jobs.py, line 9, in failing_op\n"
                                "    raise ValueError(...)\n"
                            ],
                            "className": "ValueError",
                        },
                    },
                },
                {
                    "__typename": "RunFailureEvent",
                    "runId": _FAILED_RUN_ID,
                    "message": (
                        "Execution of run for \"demo_job\" failed. Steps failed: ['failing_op']."
                    ),
                    "timestamp": "1779906299047",
                    "level": "ERROR",
                    "stepKey": None,
                    "eventType": "RUN_FAILURE",
                    "error": None,
                },
            ],
            "cursor": "cursor-end",
            "hasMore": False,
        }
    }
}

_FIXTURE_LIST_ASSETS = {
    "data": {
        "assetsOrError": {
            "__typename": "AssetConnection",
            "nodes": [
                {
                    "key": {"path": ["sales", "daily_totals"]},
                    "assetMaterializations": [
                        {
                            "timestamp": "1779900000000",
                            "runId": "abc-mat-001",
                            "partition": "2026-05-27",
                        }
                    ],
                },
                {
                    "key": {"path": ["sales", "weekly_summary"]},
                    "assetMaterializations": [],
                },
            ],
            "cursor": None,
        }
    }
}

_FIXTURE_LIST_SENSOR_TICKS = {
    "data": {
        "sensorOrError": {
            "__typename": "Sensor",
            "name": "alerting_sensor",
            "sensorState": {
                "ticks": [
                    {
                        "id": "tick-001",
                        "status": "SUCCESS",
                        "timestamp": 1779906000.0,
                        "endTimestamp": 1779906001.0,
                        "runIds": [],
                        "skipReason": None,
                        "error": None,
                    },
                    {
                        "id": "tick-002",
                        "status": "FAILURE",
                        "timestamp": 1779906300.0,
                        "endTimestamp": 1779906301.0,
                        "runIds": [],
                        "skipReason": None,
                        "error": {
                            "message": "RuntimeError: external API unreachable",
                            "stack": ["..."],
                        },
                    },
                ]
            },
        }
    }
}

# A FAILURE schedule tick errors during evaluation (e.g. invalid cron), so it
# fails BEFORE launching a run; hence runIds is empty.
_FIXTURE_LIST_SCHEDULE_TICKS = {
    "data": {
        "scheduleOrError": {
            "__typename": "Schedule",
            "name": "daily_etl_schedule",
            "scheduleState": {
                "ticks": [
                    {
                        "id": "sch-tick-001",
                        "status": "SUCCESS",
                        "timestamp": 1779820000.0,
                        "endTimestamp": 1779820002.0,
                        "runIds": ["run-001"],
                        "skipReason": None,
                        "error": None,
                    },
                    {
                        "id": "sch-tick-002",
                        "status": "FAILURE",
                        "timestamp": 1779906400.0,
                        "endTimestamp": 1779906401.0,
                        "runIds": [],
                        "skipReason": None,
                        "error": {
                            "message": "ScheduleExecutionError: cron expression invalid",
                            "stack": ["..."],
                        },
                    },
                ]
            },
        }
    }
}


def _make_mock_dagster_client(query_to_response: dict[str, dict[str, Any]]) -> httpx.Client:
    """Build an httpx.Client whose transport routes each POST to the right canned response.

    Routes by the first non-empty word of the GraphQL query (e.g. ``ListRuns`` matches
    a request whose query string contains ``query ListRuns``).
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query_text = body.get("query", "")
        for marker, payload in query_to_response.items():
            if marker in query_text:
                return httpx.Response(200, json=payload)
        return httpx.Response(
            200, json={"errors": [{"message": f"no fixture for query: {query_text[:80]}"}]}
        )

    return httpx.Client(transport=httpx.MockTransport(_handler))


@pytest.fixture
def patched_dagster_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch app.integrations.dagster._client to return a DagsterClient backed by
    a fixture httpx.Client that serves canned responses for each query."""
    mock_http = _make_mock_dagster_client(
        {
            "ListRuns": _FIXTURE_LIST_RUNS,
            "GetRunLogs": _FIXTURE_GET_RUN_LOGS,
            "ListAssets": _FIXTURE_LIST_ASSETS,
            "SensorTicks": _FIXTURE_LIST_SENSOR_TICKS,
            "ScheduleTicks": _FIXTURE_LIST_SCHEDULE_TICKS,
        }
    )

    def _fake_client(config: dagster_integration.DagsterConfig) -> DagsterClient:
        return DagsterClient(
            endpoint=config.endpoint,
            api_token=config.api_token,
            http_client=mock_http,
        )

    monkeypatch.setattr(dagster_integration, "_client", _fake_client)


# --- alert source mapping --------------------------------------------------


def test_dagster_alert_source_seeds_dagster_tools() -> None:
    """A dagster-sourced alert pre-seeds dagster tools before the ReAct loop."""
    assert "dagster" in _SEEDING_MAP
    assert _SEEDING_MAP["dagster"] == ("dagster",)


def test_dagster_alert_source_appears_in_prompt_map() -> None:
    """A dagster-sourced alert is treated as a primary dagster-tool source in the prompt."""
    assert "dagster" in _PROMPT_MAP
    assert _PROMPT_MAP["dagster"] == ("dagster",)


# --- tool scenarios --------------------------------------------------------


def test_dagster_runs_synthetic_scenario(patched_dagster_client: None) -> None:
    """A failing Dagster run is surfaced as evidence with status FAILURE."""
    result = list_dagster_runs(
        endpoint="https://example.dagster.cloud/prod",
        api_token="test-token",
        limit=5,
        status="FAILURE",
    )
    runs = result["data"]["runsOrError"]["results"]
    assert len(runs) == 1
    assert runs[0]["status"] == "FAILURE"
    assert runs[0]["jobName"] == "demo_job"
    assert runs[0]["runId"] == _FAILED_RUN_ID


def test_dagster_run_logs_synthetic_scenario(patched_dagster_client: None) -> None:
    """The event log for a failed run surfaces the underlying user-code ValueError."""
    result = get_dagster_run_logs(
        endpoint="https://example.dagster.cloud/prod",
        api_token="test-token",
        run_id=_FAILED_RUN_ID,
    )
    events = result["data"]["logsForRun"]["events"]
    step_failures = [e for e in events if e["__typename"] == "ExecutionStepFailureEvent"]
    assert len(step_failures) == 1
    cause = step_failures[0]["error"]["cause"]
    assert cause["className"] == "ValueError"
    assert "intentional failure" in cause["message"]


def test_dagster_assets_synthetic_scenario(patched_dagster_client: None) -> None:
    """Assets are returned with their latest materialization (or empty for stale)."""
    result = list_dagster_assets(
        endpoint="https://example.dagster.cloud/prod",
        api_token="test-token",
        limit=10,
    )
    nodes = result["data"]["assetsOrError"]["nodes"]
    assert len(nodes) == 2
    assert nodes[0]["assetMaterializations"][0]["runId"] == "abc-mat-001"
    # second asset has never been materialized
    assert nodes[1]["assetMaterializations"] == []


def test_dagster_sensor_ticks_synthetic_scenario(patched_dagster_client: None) -> None:
    """Sensor tick history surfaces a recent FAILURE tick with the underlying error message."""
    result = list_dagster_sensor_ticks(
        endpoint="https://example.dagster.cloud/prod",
        api_token="test-token",
        repository_location_name="my_code_location",
        repository_name="my_repo",
        sensor_name="alerting_sensor",
        limit=5,
    )
    ticks = result["data"]["sensorOrError"]["sensorState"]["ticks"]
    failure_ticks = [t for t in ticks if t["status"] == "FAILURE"]
    assert len(failure_ticks) == 1
    assert "external API unreachable" in failure_ticks[0]["error"]["message"]


def test_dagster_schedule_ticks_synthetic_scenario(patched_dagster_client: None) -> None:
    """Schedule tick history surfaces a recent FAILURE tick with the underlying error message."""
    result = list_dagster_schedule_ticks(
        endpoint="https://example.dagster.cloud/prod",
        api_token="test-token",
        repository_location_name="my_code_location",
        repository_name="my_repo",
        schedule_name="daily_etl_schedule",
        limit=5,
    )
    ticks = result["data"]["scheduleOrError"]["scheduleState"]["ticks"]
    failure_ticks = [t for t in ticks if t["status"] == "FAILURE"]
    assert len(failure_ticks) == 1
    assert "cron expression invalid" in failure_ticks[0]["error"]["message"]
