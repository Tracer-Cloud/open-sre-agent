from __future__ import annotations

import pytest

from core.llm.shared.tool_schema_normalize import build_openai_tool_specs
from integrations.grafana.tools import (
    query_grafana_annotations,
    query_grafana_logs,
    query_grafana_metrics,
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
