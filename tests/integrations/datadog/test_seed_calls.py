from __future__ import annotations

from typing import Any

from tools.investigation.stages.gather_evidence.tools import build_seed_calls
from tools.registry import get_registered_tool


def test_datadog_metric_seed_uses_public_alert_query_fields() -> None:
    state: dict[str, Any] = {
        "alert_source": "datadog",
        "raw_alert": {
            "metric_name": "opensre.demo.cpu",
            "query": "avg:opensre.demo.cpu{env:demo,service:checkout-api}",
            "time_range_minutes": 15,
        },
        "alert_json": {"alert_source": "datadog"},
        "resolved_integrations": {
            "datadog": {
                "api_key": "api-key",
                "app_key": "app-key",
                "site": "datadoghq.com",
            }
        },
    }
    registered = get_registered_tool("query_datadog_metrics")
    assert registered is not None

    calls = build_seed_calls(state, [registered], object())

    assert len(calls) == 1
    assert calls[0].input == {
        "metric_name": "opensre.demo.cpu",
        "query": "avg:opensre.demo.cpu{env:demo,service:checkout-api}",
        "time_range_minutes": 15,
    }
