from __future__ import annotations

import pytest

from core.llm.shared.tool_schema_normalize import build_openai_tool_specs
from integrations.grafana.tools import (
    query_grafana_annotations,
    query_grafana_logs,
    query_grafana_metrics,
    query_grafana_service_names,
    query_grafana_traces,
)
from tests.benchmarks.orcabench.execution.native_investigation import (
    _with_orca_time_bounds,
)


@pytest.mark.parametrize(
    "tool_function",
    [
        query_grafana_annotations,
        query_grafana_logs,
        query_grafana_metrics,
        query_grafana_service_names,
        query_grafana_traces,
    ],
)
def test_openai_tool_schema_preserves_native_time_bounds_fields(tool_function) -> None:
    registered = tool_function.__opensre_registered_tool__
    [adapted] = _with_orca_time_bounds([registered])

    [tool_spec] = build_openai_tool_specs([adapted])

    time_schema = tool_spec["function"]["parameters"]["properties"]["time_bounds"]
    assert time_schema["type"] == "object"
    assert set(time_schema["properties"]) == {
        "start_time",
        "end_time",
        "lookback_minutes",
    }
    assert adapted.retrieval_controls.time_bounds is True
    assert registered.retrieval_controls.time_bounds is False


def test_orca_native_trace_schema_keeps_direct_trace_lookup_hidden() -> None:
    [trace_tool] = _with_orca_time_bounds(
        [query_grafana_traces.__opensre_registered_tool__],
        tool_capability_mode="native",
    )

    assert "time_bounds" in trace_tool.input_schema["properties"]
    assert "action" not in trace_tool.input_schema["properties"]
    assert "trace_id" not in trace_tool.input_schema["properties"]


def test_orca_terminus_parity_schemas_expose_rich_backend_controls() -> None:
    log_tool, trace_tool = _with_orca_time_bounds(
        [
            query_grafana_logs.__opensre_registered_tool__,
            query_grafana_traces.__opensre_registered_tool__,
        ],
        tool_capability_mode="terminus_parity",
    )

    assert {"query", "sort_order", "cursor"} <= set(log_tool.input_schema["properties"])
    assert {
        "action",
        "trace_id",
        "operation",
        "tags",
        "min_duration",
        "max_duration",
    } <= set(trace_tool.input_schema["properties"])
