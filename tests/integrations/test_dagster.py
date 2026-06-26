"""Unit tests for the Dagster integration module.

Mirrors the test_rabbitmq.py pattern: config layer + GraphQL client + validation
exercised against httpx.MockTransport, no real Dagster instance required.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from integrations.dagster import (
    DagsterConfig,
    DagsterValidationResult,
    _compute_run_durations,
    _extract_step_failures,
    build_dagster_config,
    dagster_extract_params,
    dagster_is_available,
    get_run_logs,
    list_assets_with_materialization,
    list_runs,
    list_schedule_ticks,
    list_sensor_ticks,
    validate_dagster_config,
)
from vendors.dagster.client import DagsterClient

# --- helpers ---------------------------------------------------------------


def _mock_client(
    handler: object | None = None,
    *,
    responses: list[dict[str, Any]] | None = None,
    raise_on_request: BaseException | None = None,
    api_token: str = "",
) -> httpx.Client:
    """Build an httpx.Client whose transport returns canned JSON or raises.

    Pass either ``handler`` (a callable taking ``httpx.Request`` and returning
    ``httpx.Response``) OR ``responses`` (a list of dicts; each call returns
    the next dict as a 200 JSON body). ``raise_on_request`` overrides both
    and raises the given exception per request.
    """
    if raise_on_request is not None:

        def _raise(_request: httpx.Request) -> httpx.Response:
            raise raise_on_request

        return httpx.Client(
            transport=httpx.MockTransport(_raise),
            headers=_headers_for(api_token),
        )

    if handler is None and responses is not None:
        queue = list(responses)

        def _from_queue(_request: httpx.Request) -> httpx.Response:
            payload = queue.pop(0) if queue else {"data": {}}
            return httpx.Response(200, json=payload)

        handler = _from_queue

    assert handler is not None, "must pass handler or responses"
    return httpx.Client(
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        headers=_headers_for(api_token),
    )


def _headers_for(api_token: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["Dagster-Cloud-Api-Token"] = api_token
    return headers


def _read_request_body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


# --- TestDagsterConfig -----------------------------------------------------


class TestDagsterConfig:
    def test_endpoint_required(self) -> None:
        with pytest.raises(ValidationError):
            DagsterConfig()  # type: ignore[call-arg]

    def test_defaults_api_token_and_integration_id_to_empty(self) -> None:
        config = DagsterConfig(endpoint="http://localhost:3000")
        assert config.api_token == ""
        assert config.integration_id == ""

    def test_accepts_all_fields(self) -> None:
        config = DagsterConfig(
            endpoint="https://acme.eu.dagster.cloud/prod",
            api_token="t0k3n",
            integration_id="cloud-prod",
        )
        assert config.endpoint == "https://acme.eu.dagster.cloud/prod"
        assert config.api_token == "t0k3n"
        assert config.integration_id == "cloud-prod"


class TestBuildDagsterConfig:
    def test_from_dict_constructs_config(self) -> None:
        config = build_dagster_config({"endpoint": "http://localhost:3000", "api_token": "abc"})
        assert isinstance(config, DagsterConfig)
        assert config.endpoint == "http://localhost:3000"
        assert config.api_token == "abc"

    def test_from_none_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            build_dagster_config(None)


# --- TestDagsterIsAvailable / TestDagsterExtractParams ---------------------


class TestDagsterIsAvailable:
    def test_empty_sources_returns_false(self) -> None:
        assert dagster_is_available({}) is False

    def test_sources_without_endpoint_returns_false(self) -> None:
        assert dagster_is_available({"dagster": {}}) is False

    def test_sources_with_endpoint_returns_true(self) -> None:
        assert dagster_is_available({"dagster": {"endpoint": "http://x"}}) is True


class TestDagsterExtractParams:
    def test_returns_endpoint_and_token(self) -> None:
        sources = {"dagster": {"endpoint": "http://x", "api_token": "t"}}
        assert dagster_extract_params(sources) == {"endpoint": "http://x", "api_token": "t"}

    def test_missing_keys_default_to_empty(self) -> None:
        assert dagster_extract_params({}) == {"endpoint": "", "api_token": ""}


# --- TestDagsterClientUrlNormalisation -------------------------------------


class TestDagsterClientUrlNormalisation:
    @pytest.mark.parametrize(
        "raw_endpoint",
        [
            "https://example.com/prod",
            "https://example.com/prod/",
            "https://example.com/prod/graphql",
            "https://example.com/prod/graphql/",
        ],
    )
    def test_endpoint_collapses_to_canonical_base(self, raw_endpoint: str) -> None:
        # We use a mock client so no network call happens. Only normalised
        # state is inspected.
        client = DagsterClient(
            endpoint=raw_endpoint, http_client=_mock_client(responses=[{"data": {}}])
        )
        assert client.endpoint == "https://example.com/prod"
        assert client._graphql_url == "https://example.com/prod/graphql"


# --- TestDagsterClientHeaders ----------------------------------------------


class TestDagsterClientHeaders:
    def test_token_set_includes_auth_header(self) -> None:
        # When no http_client is injected, the default httpx.Client carries
        # our default headers. Inspect them directly.
        client = DagsterClient(endpoint="http://x", api_token="t0k3n")
        assert client._client.headers.get("Dagster-Cloud-Api-Token") == "t0k3n"
        assert client._client.headers.get("Content-Type") == "application/json"

    def test_token_empty_omits_auth_header(self) -> None:
        client = DagsterClient(endpoint="http://x")
        assert "Dagster-Cloud-Api-Token" not in client._client.headers
        assert client._client.headers.get("Content-Type") == "application/json"


# --- TestPing --------------------------------------------------------------


class TestPing:
    def test_happy_path_returns_data(self) -> None:
        client = DagsterClient(
            endpoint="http://x",
            http_client=_mock_client(responses=[{"data": {"version": "1.2.3"}}]),
        )
        result = client.ping()
        assert result == {"data": {"version": "1.2.3"}}

    def test_http_500_returns_error(self) -> None:
        def _handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        client = DagsterClient(endpoint="http://x", http_client=_mock_client(handler=_handler))
        result = client.ping()
        assert "error" in result
        assert "HTTP 500" in result["error"]
        assert "server error" in result["error"]

    def test_network_error_returns_error(self) -> None:
        client = DagsterClient(
            endpoint="http://x",
            http_client=_mock_client(raise_on_request=httpx.ConnectError("refused")),
        )
        result = client.ping()
        assert "error" in result
        assert "Request to Dagster failed" in result["error"]

    def test_non_json_response_returns_error(self) -> None:
        def _handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        client = DagsterClient(endpoint="http://x", http_client=_mock_client(handler=_handler))
        result = client.ping()
        assert "error" in result
        assert "Invalid JSON" in result["error"]

    def test_graphql_errors_field_returns_error(self) -> None:
        def _handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"errors": [{"message": "Field 'version' is not allowed"}]}
            )

        client = DagsterClient(endpoint="http://x", http_client=_mock_client(handler=_handler))
        result = client.ping()
        assert "error" in result
        assert "Field 'version' is not allowed" in result["error"]


# --- TestListRuns ----------------------------------------------------------


class TestListRuns:
    def test_sends_query_with_limit_no_status(self) -> None:
        captured: list[dict[str, Any]] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            captured.append(_read_request_body(request))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "runsOrError": {
                            "__typename": "Runs",
                            "results": [],
                            "count": 0,
                        }
                    }
                },
            )

        client = DagsterClient(endpoint="http://x", http_client=_mock_client(handler=_handler))
        result = client.list_runs(limit=5)
        assert "data" in result
        # Variables: limit set, statuses absent
        variables = captured[0]["variables"]
        assert variables == {"limit": 5}

    def test_sends_status_filter_as_single_element_list(self) -> None:
        captured: list[dict[str, Any]] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            captured.append(_read_request_body(request))
            return httpx.Response(200, json={"data": {"runsOrError": {"__typename": "Runs"}}})

        client = DagsterClient(endpoint="http://x", http_client=_mock_client(handler=_handler))
        client.list_runs(limit=10, status="FAILURE")
        variables = captured[0]["variables"]
        assert variables == {"limit": 10, "statuses": ["FAILURE"]}

    def test_invalid_filter_union_member_propagates(self) -> None:
        client = DagsterClient(
            endpoint="http://x",
            http_client=_mock_client(
                responses=[
                    {
                        "data": {
                            "runsOrError": {
                                "__typename": "InvalidPipelineRunsFilterError",
                                "message": "bad filter",
                            }
                        }
                    }
                ]
            ),
        )
        result = client.list_runs(limit=1)
        assert result["data"]["runsOrError"]["__typename"] == "InvalidPipelineRunsFilterError"

    def test_python_error_union_member_propagates(self) -> None:
        client = DagsterClient(
            endpoint="http://x",
            http_client=_mock_client(
                responses=[
                    {
                        "data": {
                            "runsOrError": {
                                "__typename": "PythonError",
                                "message": "internal failure",
                            }
                        }
                    }
                ]
            ),
        )
        result = client.list_runs(limit=1)
        assert result["data"]["runsOrError"]["__typename"] == "PythonError"

    def test_sends_pipeline_name_filter_when_job_name_passed(self) -> None:
        """``job_name`` is sent as ``RunsFilter.pipelineName`` (Dagster's
        back-compat alias for jobName) so list_runs scopes to one pipeline."""
        captured: list[dict[str, Any]] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            captured.append(_read_request_body(request))
            return httpx.Response(200, json={"data": {"runsOrError": {"__typename": "Runs"}}})

        client = DagsterClient(endpoint="http://x", http_client=_mock_client(handler=_handler))
        client.list_runs(limit=5, job_name="user_analytics_pipeline")
        variables = captured[0]["variables"]
        assert variables == {
            "limit": 5,
            "pipelineName": "user_analytics_pipeline",
        }

    def test_sends_pipeline_name_alongside_status_when_both_passed(self) -> None:
        """job_name and status are independent dimensions of the filter; both
        must propagate when set together."""
        captured: list[dict[str, Any]] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            captured.append(_read_request_body(request))
            return httpx.Response(200, json={"data": {"runsOrError": {"__typename": "Runs"}}})

        client = DagsterClient(endpoint="http://x", http_client=_mock_client(handler=_handler))
        client.list_runs(limit=10, status="FAILURE", job_name="user_analytics_pipeline")
        variables = captured[0]["variables"]
        assert variables == {
            "limit": 10,
            "statuses": ["FAILURE"],
            "pipelineName": "user_analytics_pipeline",
        }


# --- TestGetRunLogs --------------------------------------------------------


class TestGetRunLogs:
    def test_sends_runid_variable(self) -> None:
        captured: list[dict[str, Any]] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            captured.append(_read_request_body(request))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "logsForRun": {
                            "__typename": "EventConnection",
                            "events": [],
                            "cursor": None,
                            "hasMore": False,
                        }
                    }
                },
            )

        client = DagsterClient(endpoint="http://x", http_client=_mock_client(handler=_handler))
        client.get_run_logs(run_id="abc-123")
        variables = captured[0]["variables"]
        assert variables["runId"] == "abc-123"

    def test_sends_after_cursor_variable_when_cursor_passed(self) -> None:
        """Pin the GraphQL variable name: Dagster's logsForRun field expects
        ``afterCursor``, not ``cursor``. A wrong name silently passes our
        mock-based pagination tests but fails against a real Dagster server
        with ``Unknown argument 'cursor' on field 'logsForRun'``."""
        captured: list[dict[str, Any]] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            captured.append(_read_request_body(request))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "logsForRun": {
                            "__typename": "EventConnection",
                            "events": [],
                            "cursor": None,
                            "hasMore": False,
                        }
                    }
                },
            )

        client = DagsterClient(endpoint="http://x", http_client=_mock_client(handler=_handler))
        client.get_run_logs(run_id="abc-123", cursor="page-2-cursor")
        variables = captured[0]["variables"]
        # Must be `afterCursor` per Dagster's schema, never `cursor`.
        assert variables.get("afterCursor") == "page-2-cursor"
        assert "cursor" not in variables

    def test_run_not_found_union_member_propagates(self) -> None:
        client = DagsterClient(
            endpoint="http://x",
            http_client=_mock_client(
                responses=[
                    {
                        "data": {
                            "logsForRun": {
                                "__typename": "RunNotFoundError",
                                "message": "Run not found",
                            }
                        }
                    }
                ]
            ),
        )
        result = client.get_run_logs(run_id="missing")
        assert result["data"]["logsForRun"]["__typename"] == "RunNotFoundError"


# --- TestExtractStepFailures -------------------------------------------------


class TestExtractStepFailures:
    """The integration enriches get_run_logs responses with a structured
    `summary.failures` list so the agent's diagnosis stage can identify
    multi-failure runs without walking the raw event list."""

    def test_empty_events_yields_zero_failure_count(self) -> None:
        summary = _extract_step_failures({"events": []})
        assert summary == {"failure_count": 0, "failures": []}

    def test_non_failure_events_are_filtered_out(self) -> None:
        events = [
            {"__typename": "RunStartEvent", "stepKey": None},
            {"__typename": "StepWorkerStartedEvent", "stepKey": "op_a"},
            {"__typename": "EngineEvent", "stepKey": None},
        ]
        summary = _extract_step_failures({"events": events})
        assert summary == {"failure_count": 0, "failures": []}

    def test_single_step_failure_extracts_cause(self) -> None:
        events = [
            {
                "__typename": "ExecutionStepFailureEvent",
                "stepKey": "failing_op",
                "timestamp": "1779906298920",
                "error": {
                    "className": "DagsterExecutionStepExecutionError",
                    "cause": {
                        "className": "ValueError",
                        "message": "ValueError: intentional failure",
                    },
                },
            }
        ]
        summary = _extract_step_failures({"events": events})
        assert summary["failure_count"] == 1
        assert summary["failures"] == [
            {
                "step_key": "failing_op",
                "timestamp": "1779906298920",
                "wrapper_class": "DagsterExecutionStepExecutionError",
                "exception_class": "ValueError",
                "cause_message": "ValueError: intentional failure",
            }
        ]

    def test_multiple_concurrent_step_failures_all_surfaced(self) -> None:
        """Multi-step parallel failure case: BOTH step failures must appear
        in the summary, not just the first one. This is the agent-side
        clarity our integration owes for parallel-execution runs."""
        events = [
            {
                "__typename": "ExecutionStepFailureEvent",
                "stepKey": "extract_orders",
                "timestamp": "1",
                "error": {
                    "className": "DagsterExecutionStepExecutionError",
                    "cause": {
                        "className": "TimeoutError",
                        "message": "Postgres pool exhausted",
                    },
                },
            },
            {
                "__typename": "ExecutionStepFailureEvent",
                "stepKey": "extract_customers",
                "timestamp": "2",
                "error": {
                    "className": "DagsterExecutionStepExecutionError",
                    "cause": {
                        "className": "PermissionError",
                        "message": "Cassandra denied",
                    },
                },
            },
            {
                "__typename": "RunFailureEvent",
                "stepKey": None,
                "error": None,
            },
        ]
        summary = _extract_step_failures({"events": events})
        assert summary["failure_count"] == 2
        step_keys = [f["step_key"] for f in summary["failures"]]
        assert step_keys == ["extract_orders", "extract_customers"]
        classes = [f["exception_class"] for f in summary["failures"]]
        assert classes == ["TimeoutError", "PermissionError"]

    def test_run_failure_event_with_null_error_is_not_a_step_failure(self) -> None:
        """RunFailureEvent (run-level termination) is distinct from
        ExecutionStepFailureEvent (step-level exception). Run-level
        events often have ``error: null`` and carry no cause; the summary
        should only count step-level failures since those have the
        actionable user-code exception."""
        events = [{"__typename": "RunFailureEvent", "stepKey": None, "error": None}]
        summary = _extract_step_failures({"events": events})
        assert summary["failure_count"] == 0

    def test_step_failure_event_with_null_error_yields_null_fields(self) -> None:
        """Defensive path: ``ExecutionStepFailureEvent`` with ``error: None`` still
        counts as a failure; the wrapper/exception/cause fields fall back to None."""
        events = [
            {
                "__typename": "ExecutionStepFailureEvent",
                "stepKey": "failing_op",
                "timestamp": "1",
                "error": None,
            }
        ]
        summary = _extract_step_failures({"events": events})
        assert summary["failure_count"] == 1
        assert summary["failures"][0] == {
            "step_key": "failing_op",
            "timestamp": "1",
            "wrapper_class": None,
            "exception_class": None,
            "cause_message": None,
        }

    def test_get_run_logs_response_includes_summary_on_event_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The end-to-end integration helper enriches the EventConnection
        response with a top-level summary block."""
        client = DagsterClient(
            endpoint="http://x",
            http_client=_mock_client(
                responses=[
                    {
                        "data": {
                            "logsForRun": {
                                "__typename": "EventConnection",
                                "events": [
                                    {
                                        "__typename": "ExecutionStepFailureEvent",
                                        "stepKey": "op_x",
                                        "timestamp": "1",
                                        "error": {
                                            "className": "DagsterExecutionStepExecutionError",
                                            "cause": {
                                                "className": "RuntimeError",
                                                "message": "boom",
                                            },
                                        },
                                    }
                                ],
                                "cursor": None,
                                "hasMore": False,
                            }
                        }
                    }
                ]
            ),
        )

        from integrations import dagster as dagster_module
        from integrations.dagster import get_run_logs as helper_get_run_logs

        monkeypatch.setattr(dagster_module, "_client", lambda _cfg: client)
        result = helper_get_run_logs(DagsterConfig(endpoint="http://x"), run_id="abc")

        assert "summary" in result
        assert result["summary"]["failure_count"] == 1
        assert result["summary"]["failures"][0]["exception_class"] == "RuntimeError"

    def test_get_run_logs_response_skips_summary_on_error_union_members(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When logsForRun returns RunNotFoundError or PythonError instead
        of EventConnection, there is no event list to summarize. The
        response should NOT add a misleading empty summary block."""
        client = DagsterClient(
            endpoint="http://x",
            http_client=_mock_client(
                responses=[
                    {
                        "data": {
                            "logsForRun": {
                                "__typename": "RunNotFoundError",
                                "message": "Run not found",
                            }
                        }
                    }
                ]
            ),
        )

        from integrations import dagster as dagster_module
        from integrations.dagster import get_run_logs as helper_get_run_logs

        monkeypatch.setattr(dagster_module, "_client", lambda _cfg: client)
        result = helper_get_run_logs(DagsterConfig(endpoint="http://x"), run_id="missing")

        assert "summary" not in result

    def test_get_run_logs_paginates_until_has_more_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A run spanning multiple pages: every page's failures must roll up into
        the summary"""

        def _failure_event(step_key: str, exc_class: str) -> dict[str, Any]:
            return {
                "__typename": "ExecutionStepFailureEvent",
                "stepKey": step_key,
                "timestamp": "1",
                "error": {
                    "className": "DagsterExecutionStepExecutionError",
                    "cause": {"className": exc_class, "message": f"{exc_class} on {step_key}"},
                },
            }

        pages = [
            {
                "data": {
                    "logsForRun": {
                        "__typename": "EventConnection",
                        "events": [_failure_event("op_a", "TimeoutError")],
                        "cursor": "page-2-cursor",
                        "hasMore": True,
                    }
                }
            },
            {
                "data": {
                    "logsForRun": {
                        "__typename": "EventConnection",
                        "events": [_failure_event("op_b", "PermissionError")],
                        "cursor": "end",
                        "hasMore": False,
                    }
                }
            },
        ]

        captured_cursors: list[str | None] = []

        def fake_get_run_logs(
            self: DagsterClient,
            *,
            run_id: str,
            limit: int = 250,
            cursor: str | None = None,
        ) -> dict[str, Any]:
            captured_cursors.append(cursor)
            return pages.pop(0)

        from integrations import dagster as dagster_module
        from integrations.dagster import get_run_logs as helper_get_run_logs

        monkeypatch.setattr(DagsterClient, "get_run_logs", fake_get_run_logs)
        monkeypatch.setattr(
            dagster_module, "_client", lambda cfg: DagsterClient(endpoint=cfg.endpoint)
        )
        result = helper_get_run_logs(DagsterConfig(endpoint="http://x"), run_id="run-1")

        # Pagination drove 2 calls, with the second using the cursor from the first.
        assert captured_cursors == [None, "page-2-cursor"]
        # Both failures are present in the aggregated summary.
        assert result["summary"]["failure_count"] == 2
        assert [f["step_key"] for f in result["summary"]["failures"]] == ["op_a", "op_b"]
        # Truncation flag is false when we reached has_more=False naturally.
        assert result["summary"]["truncated"] is False
        assert result["summary"]["events_examined"] == 2

    def test_get_run_logs_caps_non_failure_events_but_keeps_late_failures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Load-bearing guarantee: a failure that arrives AFTER the non-failure
        event cap is hit must still be collected and surfaced in the summary."""
        from integrations.dagster import MAX_NON_FAILURE_RUN_LOG_EVENTS

        def _benign(i: int) -> dict[str, Any]:
            return {"__typename": "EngineEvent", "stepKey": None, "timestamp": str(i)}

        late_failure = {
            "__typename": "ExecutionStepFailureEvent",
            "stepKey": "deep_step",
            "timestamp": "9999",
            "error": {
                "className": "DagsterExecutionStepExecutionError",
                "cause": {"className": "RuntimeError", "message": "found past the cap"},
            },
        }

        # Page 1: enough benign events to fill the cap exactly.
        # Page 2: one more benign (discarded — cap reached) plus the late failure.
        pages = [
            {
                "data": {
                    "logsForRun": {
                        "__typename": "EventConnection",
                        "events": [_benign(i) for i in range(MAX_NON_FAILURE_RUN_LOG_EVENTS)],
                        "cursor": "page-2",
                        "hasMore": True,
                    }
                }
            },
            {
                "data": {
                    "logsForRun": {
                        "__typename": "EventConnection",
                        "events": [_benign(99999), late_failure],
                        "cursor": "end",
                        "hasMore": False,
                    }
                }
            },
        ]

        def fake_get_run_logs(
            self: DagsterClient,
            *,
            run_id: str,
            limit: int = 250,
            cursor: str | None = None,
        ) -> dict[str, Any]:
            return pages.pop(0)

        from integrations import dagster as dagster_module
        from integrations.dagster import get_run_logs as helper_get_run_logs

        monkeypatch.setattr(DagsterClient, "get_run_logs", fake_get_run_logs)
        monkeypatch.setattr(
            dagster_module, "_client", lambda cfg: DagsterClient(endpoint=cfg.endpoint)
        )
        result = helper_get_run_logs(DagsterConfig(endpoint="http://x"), run_id="late-fail")

        # Failure past the non-failure cap was captured.
        assert result["summary"]["failure_count"] == 1
        assert result["summary"]["failures"][0]["step_key"] == "deep_step"
        # Truncation flag still raised because non-failure data is incomplete.
        assert result["summary"]["truncated"] is True
        # Aggregated events = capped non-failure (1500) + all failures (1) = 1501.
        assert result["summary"]["events_examined"] == MAX_NON_FAILURE_RUN_LOG_EVENTS + 1

    def test_get_run_logs_sliding_window_drops_oldest_non_failures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When more non-failure events arrive than the window can hold, the
        OLDEST events are evicted (FIFO). This keeps the surviving context
        adjacent to failures (which typically land later in the stream),
        rather than at the run's chronological start."""
        from integrations.dagster import MAX_NON_FAILURE_RUN_LOG_EVENTS

        def _benign(i: int) -> dict[str, Any]:
            return {"__typename": "EngineEvent", "stepKey": None, "timestamp": str(i)}

        # Page 1 fills the window exactly; page 2 adds 2 more, forcing the
        # 2 oldest to evict.
        pages = [
            {
                "data": {
                    "logsForRun": {
                        "__typename": "EventConnection",
                        "events": [_benign(i) for i in range(MAX_NON_FAILURE_RUN_LOG_EVENTS)],
                        "cursor": "page-2",
                        "hasMore": True,
                    }
                }
            },
            {
                "data": {
                    "logsForRun": {
                        "__typename": "EventConnection",
                        "events": [
                            _benign(MAX_NON_FAILURE_RUN_LOG_EVENTS),
                            _benign(MAX_NON_FAILURE_RUN_LOG_EVENTS + 1),
                        ],
                        "cursor": "end",
                        "hasMore": False,
                    }
                }
            },
        ]

        def fake_get_run_logs(
            self: DagsterClient,
            *,
            run_id: str,
            limit: int = 250,
            cursor: str | None = None,
        ) -> dict[str, Any]:
            return pages.pop(0)

        from integrations import dagster as dagster_module
        from integrations.dagster import get_run_logs as helper_get_run_logs

        monkeypatch.setattr(DagsterClient, "get_run_logs", fake_get_run_logs)
        monkeypatch.setattr(
            dagster_module, "_client", lambda cfg: DagsterClient(endpoint=cfg.endpoint)
        )
        result = helper_get_run_logs(DagsterConfig(endpoint="http://x"), run_id="sliding")

        events = result["data"]["logsForRun"]["events"]
        timestamps = [e["timestamp"] for e in events]

        # The kept window is exactly the cap size.
        assert len(events) == MAX_NON_FAILURE_RUN_LOG_EVENTS
        # Two newest events are present (most recent context preserved).
        assert timestamps[-1] == str(MAX_NON_FAILURE_RUN_LOG_EVENTS + 1)
        assert timestamps[-2] == str(MAX_NON_FAILURE_RUN_LOG_EVENTS)
        # Two oldest events were evicted from the window.
        assert "0" not in timestamps
        assert "1" not in timestamps
        # Truncation flag set because the window overflowed.
        assert result["summary"]["truncated"] is True

    def test_get_run_logs_aggregated_events_sorted_chronologically(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Aggregated events must preserve chronological order even when a
        non-failure event (e.g. a downstream skip) lands AFTER the upstream
        failure that caused it."""
        page = {
            "data": {
                "logsForRun": {
                    "__typename": "EventConnection",
                    "events": [
                        # T=100: upstream step started
                        {"__typename": "EngineEvent", "stepKey": "start", "timestamp": "100"},
                        # T=200: upstream step FAILED (kept in failure_events)
                        {
                            "__typename": "ExecutionStepFailureEvent",
                            "stepKey": "upstream_op",
                            "timestamp": "200",
                            "error": {
                                "className": "DagsterExecutionStepExecutionError",
                                "cause": {"className": "ValueError", "message": "boom"},
                            },
                        },
                        # T=300: downstream step skipped (kept in non_failure_events)
                        {
                            "__typename": "EngineEvent",
                            "stepKey": "downstream_op",
                            "timestamp": "300",
                        },
                    ],
                    "cursor": "end",
                    "hasMore": False,
                }
            }
        }

        def fake_get_run_logs(
            self: DagsterClient,
            *,
            run_id: str,
            limit: int = 250,
            cursor: str | None = None,
        ) -> dict[str, Any]:
            return page

        from integrations import dagster as dagster_module
        from integrations.dagster import get_run_logs as helper_get_run_logs

        monkeypatch.setattr(DagsterClient, "get_run_logs", fake_get_run_logs)
        monkeypatch.setattr(
            dagster_module, "_client", lambda cfg: DagsterClient(endpoint=cfg.endpoint)
        )
        result = helper_get_run_logs(DagsterConfig(endpoint="http://x"), run_id="ordering")
        events = result["data"]["logsForRun"]["events"]
        timestamps = [int(e["timestamp"]) for e in events]
        # Must be chronological (100, 200, 300), NOT non-failures-first (100, 300, 200).
        assert timestamps == [100, 200, 300]

    def test_get_run_logs_mid_pagination_error_preserves_collected_failures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If page N>1 returns an error, failures already collected from
        earlier pages should be preserved."""
        failure_event = {
            "__typename": "ExecutionStepFailureEvent",
            "stepKey": "page_1_op",
            "timestamp": "100",
            "error": {
                "className": "DagsterExecutionStepExecutionError",
                "cause": {"className": "ValueError", "message": "boom"},
            },
        }
        pages = [
            {
                "data": {
                    "logsForRun": {
                        "__typename": "EventConnection",
                        "events": [failure_event],
                        "cursor": "page-2",
                        "hasMore": True,
                    }
                }
            },
            {"error": "Request to Dagster failed: timeout after 10s"},
        ]

        def fake_get_run_logs(
            self: DagsterClient,
            *,
            run_id: str,
            limit: int = 250,
            cursor: str | None = None,
        ) -> dict[str, Any]:
            return pages.pop(0)

        from integrations import dagster as dagster_module
        from integrations.dagster import get_run_logs as helper_get_run_logs

        monkeypatch.setattr(DagsterClient, "get_run_logs", fake_get_run_logs)
        monkeypatch.setattr(
            dagster_module, "_client", lambda cfg: DagsterClient(endpoint=cfg.endpoint)
        )
        result = helper_get_run_logs(DagsterConfig(endpoint="http://x"), run_id="partial")

        # Page-1 failure preserved
        assert result["summary"]["failure_count"] == 1
        assert result["summary"]["failures"][0]["step_key"] == "page_1_op"
        # Truncation flag set because fetch was incomplete.
        assert result["summary"]["truncated"] is True
        # fetch_error signals partial fetch with the original error message.
        assert result["summary"]["fetch_error"] == ("Request to Dagster failed: timeout after 10s")

    def test_get_run_logs_first_page_error_still_propagates_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First-page errors have no accumulated data to save.
        so the raw envelope propagates unchanged."""

        def fake_get_run_logs(
            self: DagsterClient,
            *,
            run_id: str,
            limit: int = 250,
            cursor: str | None = None,
        ) -> dict[str, Any]:
            return {"error": "Request to Dagster failed: connection refused"}

        from integrations import dagster as dagster_module
        from integrations.dagster import get_run_logs as helper_get_run_logs

        monkeypatch.setattr(DagsterClient, "get_run_logs", fake_get_run_logs)
        monkeypatch.setattr(
            dagster_module, "_client", lambda cfg: DagsterClient(endpoint=cfg.endpoint)
        )
        result = helper_get_run_logs(DagsterConfig(endpoint="http://x"), run_id="dead")

        assert result == {"error": "Request to Dagster failed: connection refused"}
        assert "summary" not in result

    def test_get_run_logs_mid_pagination_non_event_response_preserves_collected_failures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If page N>1 returns a non-EventConnection failures already collected from
        earlier pages should be preserved."""
        failure_event = {
            "__typename": "ExecutionStepFailureEvent",
            "stepKey": "page_1_op",
            "timestamp": "100",
            "error": {
                "className": "DagsterExecutionStepExecutionError",
                "cause": {"className": "ValueError", "message": "boom"},
            },
        }
        pages = [
            {
                "data": {
                    "logsForRun": {
                        "__typename": "EventConnection",
                        "events": [failure_event],
                        "cursor": "page-2",
                        "hasMore": True,
                    }
                }
            },
            {
                "data": {
                    "logsForRun": {
                        "__typename": "RunNotFoundError",
                        "message": "Run not found",
                    }
                }
            },
        ]

        def fake_get_run_logs(
            self: DagsterClient,
            *,
            run_id: str,
            limit: int = 250,
            cursor: str | None = None,
        ) -> dict[str, Any]:
            return pages.pop(0)

        from integrations import dagster as dagster_module
        from integrations.dagster import get_run_logs as helper_get_run_logs

        monkeypatch.setattr(DagsterClient, "get_run_logs", fake_get_run_logs)
        monkeypatch.setattr(
            dagster_module, "_client", lambda cfg: DagsterClient(endpoint=cfg.endpoint)
        )
        result = helper_get_run_logs(DagsterConfig(endpoint="http://x"), run_id="vanished")

        # Page-1 failure preserved
        assert result["summary"]["failure_count"] == 1
        assert result["summary"]["failures"][0]["step_key"] == "page_1_op"
        assert result["summary"]["truncated"] is True
        # fetch_error names the unexpected typename.
        assert "RunNotFoundError" in result["summary"]["fetch_error"]
        assert "page 2" in result["summary"]["fetch_error"]

    def test_get_run_logs_page_cap_safety_net(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A server that says ``hasMore=true`` forever must terminate at
        ``MAX_RUN_LOG_PAGES``; ``truncated`` is set to signal incomplete fetch."""
        from integrations.dagster import MAX_RUN_LOG_PAGES

        pages_served = [0]

        def fake_get_run_logs(
            self: DagsterClient,
            *,
            run_id: str,
            limit: int = 250,
            cursor: str | None = None,
        ) -> dict[str, Any]:
            pages_served[0] += 1
            return {
                "data": {
                    "logsForRun": {
                        "__typename": "EventConnection",
                        "events": [
                            {"__typename": "EngineEvent", "stepKey": None, "timestamp": "1"}
                        ],
                        "cursor": f"page-{pages_served[0]}",
                        "hasMore": True,
                    }
                }
            }

        from integrations import dagster as dagster_module
        from integrations.dagster import get_run_logs as helper_get_run_logs

        monkeypatch.setattr(DagsterClient, "get_run_logs", fake_get_run_logs)
        monkeypatch.setattr(
            dagster_module, "_client", lambda cfg: DagsterClient(endpoint=cfg.endpoint)
        )
        result = helper_get_run_logs(DagsterConfig(endpoint="http://x"), run_id="runaway")

        assert pages_served[0] == MAX_RUN_LOG_PAGES
        assert result["summary"]["truncated"] is True
        assert result["data"]["logsForRun"]["hasMore"] is True

    def test_get_run_logs_exits_when_has_more_true_but_cursor_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defensive: a buggy server that says ``hasMore=true`` but returns no
        cursor should exit the loop safely and signal partial fetch"""
        called = [0]

        def fake_get_run_logs(
            self: DagsterClient,
            *,
            run_id: str,
            limit: int = 250,
            cursor: str | None = None,
        ) -> dict[str, Any]:
            called[0] += 1
            return {
                "data": {
                    "logsForRun": {
                        "__typename": "EventConnection",
                        "events": [],
                        "cursor": None,
                        "hasMore": True,
                    }
                }
            }

        from integrations import dagster as dagster_module
        from integrations.dagster import get_run_logs as helper_get_run_logs

        monkeypatch.setattr(DagsterClient, "get_run_logs", fake_get_run_logs)
        monkeypatch.setattr(
            dagster_module, "_client", lambda cfg: DagsterClient(endpoint=cfg.endpoint)
        )
        result = helper_get_run_logs(DagsterConfig(endpoint="http://x"), run_id="buggy")

        # Loop exited safely after one call (no infinite loop).
        assert called[0] == 1
        # Truncation signal raised because pagination ended mid-stream.
        assert result["summary"]["truncated"] is True
        assert "hasMore=true" in result["summary"]["fetch_error"]
        assert "no cursor" in result["summary"]["fetch_error"]


# --- TestComputeRunDurations -----------------------------------------------


class TestComputeRunDurations:
    """The integration adds ``duration_seconds`` per run row so the agent does
    not have to derive it from ``startTime``/``endTime`` itself."""

    def test_completed_run_yields_finite_duration(self) -> None:
        result = {
            "data": {
                "runsOrError": {
                    "__typename": "Runs",
                    "results": [
                        {"runId": "r1", "startTime": 100.0, "endTime": 142.5},
                    ],
                }
            }
        }
        enriched = _compute_run_durations(result)
        assert enriched["data"]["runsOrError"]["results"][0]["duration_seconds"] == 42.5

    def test_in_flight_run_yields_null_duration(self) -> None:
        result = {
            "data": {
                "runsOrError": {
                    "__typename": "Runs",
                    "results": [
                        {"runId": "r2", "startTime": 100.0, "endTime": None},
                    ],
                }
            }
        }
        enriched = _compute_run_durations(result)
        assert enriched["data"]["runsOrError"]["results"][0]["duration_seconds"] is None

    def test_queued_run_with_no_timestamps_yields_null(self) -> None:
        result = {
            "data": {
                "runsOrError": {
                    "__typename": "Runs",
                    "results": [
                        {"runId": "r3", "startTime": None, "endTime": None},
                    ],
                }
            }
        }
        enriched = _compute_run_durations(result)
        assert enriched["data"]["runsOrError"]["results"][0]["duration_seconds"] is None

    def test_error_union_member_skipped(self) -> None:
        """``InvalidPipelineRunsFilterError`` and ``PythonError`` have no
        ``results`` array; the enrichment is a no-op."""
        result = {
            "data": {
                "runsOrError": {
                    "__typename": "InvalidPipelineRunsFilterError",
                    "message": "bad filter",
                }
            }
        }
        enriched = _compute_run_durations(result)
        assert enriched == result

    def test_python_error_union_member_is_no_op(self) -> None:
        result = {
            "data": {
                "runsOrError": {
                    "__typename": "PythonError",
                    "message": "internal failure",
                }
            }
        }
        enriched = _compute_run_durations(result)
        assert enriched == result

    def test_multi_row_response_enriches_every_row(self) -> None:
        result = {
            "data": {
                "runsOrError": {
                    "__typename": "Runs",
                    "results": [
                        {"runId": "r1", "startTime": 100.0, "endTime": 150.0},
                        {"runId": "r2", "startTime": 200.0, "endTime": None},
                        {"runId": "r3", "startTime": 300.0, "endTime": 305.5},
                    ],
                }
            }
        }
        enriched = _compute_run_durations(result)
        durations = [r["duration_seconds"] for r in enriched["data"]["runsOrError"]["results"]]
        assert durations == [50.0, None, 5.5]


# --- TestListAssetsWithMaterialization -------------------------------------


class TestListAssetsWithMaterialization:
    def test_sends_limit_variable_and_parses_asset_connection(self) -> None:
        captured: list[dict[str, Any]] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            captured.append(_read_request_body(request))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "assetsOrError": {
                            "__typename": "AssetConnection",
                            "nodes": [],
                            "cursor": None,
                        }
                    }
                },
            )

        client = DagsterClient(endpoint="http://x", http_client=_mock_client(handler=_handler))
        result = client.list_assets_with_materialization(limit=7)
        assert captured[0]["variables"] == {"limit": 7}
        assert result["data"]["assetsOrError"]["__typename"] == "AssetConnection"


# --- TestListSensorTicks ---------------------------------------------------


class TestListSensorTicks:
    def test_sends_sensor_selector_with_all_three_coordinates(self) -> None:
        captured: list[dict[str, Any]] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            captured.append(_read_request_body(request))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "sensorOrError": {
                            "__typename": "Sensor",
                            "name": "my_sensor",
                            "sensorState": {"ticks": []},
                        }
                    }
                },
            )

        client = DagsterClient(endpoint="http://x", http_client=_mock_client(handler=_handler))
        client.list_sensor_ticks(
            repository_name="repo_x",
            repository_location_name="loc_y",
            sensor_name="my_sensor",
            limit=3,
        )
        variables = captured[0]["variables"]
        assert variables["sensorSelector"] == {
            "repositoryName": "repo_x",
            "repositoryLocationName": "loc_y",
            "sensorName": "my_sensor",
        }
        assert variables["limit"] == 3

    def test_sensor_not_found_union_member_propagates(self) -> None:
        client = DagsterClient(
            endpoint="http://x",
            http_client=_mock_client(
                responses=[
                    {
                        "data": {
                            "sensorOrError": {
                                "__typename": "SensorNotFoundError",
                                "message": "Sensor not found",
                            }
                        }
                    }
                ]
            ),
        )
        result = client.list_sensor_ticks(
            repository_name="r",
            repository_location_name="l",
            sensor_name="missing",
        )
        assert result["data"]["sensorOrError"]["__typename"] == "SensorNotFoundError"


# --- TestListScheduleTicks -------------------------------------------------


class TestListScheduleTicks:
    def test_sends_schedule_selector_with_all_three_coordinates(self) -> None:
        captured: list[dict[str, Any]] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            captured.append(_read_request_body(request))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "scheduleOrError": {
                            "__typename": "Schedule",
                            "name": "my_schedule",
                            "scheduleState": {"ticks": []},
                        }
                    }
                },
            )

        client = DagsterClient(endpoint="http://x", http_client=_mock_client(handler=_handler))
        client.list_schedule_ticks(
            repository_name="repo_x",
            repository_location_name="loc_y",
            schedule_name="my_schedule",
            limit=4,
        )
        variables = captured[0]["variables"]
        assert variables["scheduleSelector"] == {
            "repositoryName": "repo_x",
            "repositoryLocationName": "loc_y",
            "scheduleName": "my_schedule",
        }
        assert variables["limit"] == 4

    def test_schedule_not_found_union_member_propagates(self) -> None:
        client = DagsterClient(
            endpoint="http://x",
            http_client=_mock_client(
                responses=[
                    {
                        "data": {
                            "scheduleOrError": {
                                "__typename": "ScheduleNotFoundError",
                                "message": "Schedule not found",
                            }
                        }
                    }
                ]
            ),
        )
        result = client.list_schedule_ticks(
            repository_name="r",
            repository_location_name="l",
            schedule_name="missing",
        )
        assert result["data"]["scheduleOrError"]["__typename"] == "ScheduleNotFoundError"


# --- TestValidateDagsterConfig ---------------------------------------------


class TestValidateDagsterConfig:
    def test_missing_endpoint_returns_not_ok(self) -> None:
        config = DagsterConfig(endpoint="")
        result = validate_dagster_config(config)
        assert isinstance(result, DagsterValidationResult)
        assert result.ok is False
        assert "endpoint is required" in result.detail.lower()

    def test_happy_path_returns_ok_with_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original_init = DagsterClient.__init__

        def patched_init(
            self: DagsterClient, endpoint: str, api_token: str = "", timeout_s: int = 10
        ) -> None:
            original_init(
                self,
                endpoint=endpoint,
                api_token=api_token,
                timeout_s=timeout_s,
                http_client=_mock_client(responses=[{"data": {"version": "1.13.6"}}]),
            )

        monkeypatch.setattr(DagsterClient, "__init__", patched_init)

        result = validate_dagster_config(DagsterConfig(endpoint="http://x"))
        assert result.ok is True
        assert "1.13.6" in result.detail

    def test_probe_failure_returns_not_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original_init = DagsterClient.__init__

        def patched_init(
            self: DagsterClient, endpoint: str, api_token: str = "", timeout_s: int = 10
        ) -> None:
            original_init(
                self,
                endpoint=endpoint,
                api_token=api_token,
                timeout_s=timeout_s,
                http_client=_mock_client(raise_on_request=httpx.ConnectError("refused")),
            )

        monkeypatch.setattr(DagsterClient, "__init__", patched_init)

        result = validate_dagster_config(DagsterConfig(endpoint="http://x"))
        assert result.ok is False
        assert "probe failed" in result.detail.lower()

    def test_response_without_version_field_returns_not_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_init = DagsterClient.__init__

        def patched_init(
            self: DagsterClient, endpoint: str, api_token: str = "", timeout_s: int = 10
        ) -> None:
            original_init(
                self,
                endpoint=endpoint,
                api_token=api_token,
                timeout_s=timeout_s,
                http_client=_mock_client(responses=[{"data": {}}]),
            )

        monkeypatch.setattr(DagsterClient, "__init__", patched_init)

        result = validate_dagster_config(DagsterConfig(endpoint="http://x"))
        assert result.ok is False
        assert "did not return a version" in result.detail.lower()


# --- TestIntegrationHelpers ------------------------------------------------


class TestIntegrationHelpers:
    """The list_runs / get_run_logs / list_assets / list_sensor_ticks helpers
    in integrations.dagster are thin wrappers that build a DagsterClient
    and call through. Cover that the routing works."""

    def test_list_runs_routes_to_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []

        def fake_list_runs(
            self: DagsterClient,
            *,
            limit: int = 25,
            status: str | None = None,
            job_name: str | None = None,
        ) -> dict[str, Any]:
            calls.append({"limit": limit, "status": status, "job_name": job_name})
            return {"data": {"routed": True}}

        monkeypatch.setattr(DagsterClient, "list_runs", fake_list_runs)
        result = list_runs(
            DagsterConfig(endpoint="http://x"),
            limit=3,
            status="FAILURE",
            job_name="user_analytics_pipeline",
        )
        assert calls == [{"limit": 3, "status": "FAILURE", "job_name": "user_analytics_pipeline"}]
        assert result == {"data": {"routed": True}}

    def test_get_run_logs_routes_to_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []

        def fake_get_run_logs(
            self: DagsterClient,
            *,
            run_id: str,
            limit: int = 250,
            cursor: str | None = None,
        ) -> dict[str, Any]:
            calls.append({"run_id": run_id, "limit": limit, "cursor": cursor})
            return {"data": {"routed": True}}

        monkeypatch.setattr(DagsterClient, "get_run_logs", fake_get_run_logs)
        result = get_run_logs(DagsterConfig(endpoint="http://x"), run_id="abc")
        # The first (and only) call has cursor=None and the default page size.
        assert calls == [{"run_id": "abc", "limit": 250, "cursor": None}]
        # Non-EventConnection payload propagates as-is, no pagination.
        assert result == {"data": {"routed": True}}

    def test_list_assets_routes_to_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []

        def fake(self: DagsterClient, *, limit: int = 25) -> dict[str, Any]:
            calls.append({"limit": limit})
            return {"data": {"routed": True}}

        monkeypatch.setattr(DagsterClient, "list_assets_with_materialization", fake)
        result = list_assets_with_materialization(DagsterConfig(endpoint="http://x"), limit=4)
        assert calls == [{"limit": 4}]
        assert result == {"data": {"routed": True}}

    def test_list_sensor_ticks_routes_to_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []

        def fake(
            self: DagsterClient,
            *,
            repository_name: str,
            repository_location_name: str,
            sensor_name: str,
            limit: int = 25,
        ) -> dict[str, Any]:
            calls.append(
                {
                    "repository_name": repository_name,
                    "repository_location_name": repository_location_name,
                    "sensor_name": sensor_name,
                    "limit": limit,
                }
            )
            return {"data": {"routed": True}}

        monkeypatch.setattr(DagsterClient, "list_sensor_ticks", fake)
        result = list_sensor_ticks(
            DagsterConfig(endpoint="http://x"),
            repository_name="r",
            repository_location_name="loc",
            sensor_name="s",
            limit=2,
        )
        assert calls == [
            {
                "repository_name": "r",
                "repository_location_name": "loc",
                "sensor_name": "s",
                "limit": 2,
            }
        ]
        assert result == {"data": {"routed": True}}

    def test_list_schedule_ticks_routes_to_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []

        def fake(
            self: DagsterClient,
            *,
            repository_name: str,
            repository_location_name: str,
            schedule_name: str,
            limit: int = 25,
        ) -> dict[str, Any]:
            calls.append(
                {
                    "repository_name": repository_name,
                    "repository_location_name": repository_location_name,
                    "schedule_name": schedule_name,
                    "limit": limit,
                }
            )
            return {"data": {"routed": True}}

        monkeypatch.setattr(DagsterClient, "list_schedule_ticks", fake)
        result = list_schedule_ticks(
            DagsterConfig(endpoint="http://x"),
            repository_name="r",
            repository_location_name="loc",
            schedule_name="s",
            limit=2,
        )
        assert calls == [
            {
                "repository_name": "r",
                "repository_location_name": "loc",
                "schedule_name": "s",
                "limit": 2,
            }
        ]
        assert result == {"data": {"routed": True}}
