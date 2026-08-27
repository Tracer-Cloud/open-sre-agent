from typing import Any

import pytest

from core.tool_framework.utils import tool_unavailable
from integrations.victoria_logs.tools import _map_victoria_logs_query


def test_victoria_logs_mapper_records_an_entry_counting_rows() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "source": "victoria_logs",
        "available": True,
        "rows": [
            {"_time": "2026-05-26T16:43:18Z", "_msg": "connection timeout in payment service"},
            {"_time": "2026-05-26T16:43:19Z", "_msg": "failed to acquire lock"},
        ],
        "total": 2,
    }

    _map_victoria_logs_query(evidence, output, {})

    assert evidence["catalog_entries"] == [
        {
            "source": "victoria_logs_query",
            "label": "VictoriaLogs",
            "summary": "2 log entries",
            "url": None,
            "snippet": None,
        }
    ]


@pytest.mark.parametrize(
    "output",
    [
        pytest.param({}, id="empty-payload"),
        pytest.param(
            tool_unavailable("victoria_logs", "connection refused", rows=[], total=0),
            id="unavailable-envelope",
        ),
        pytest.param(
            {"source": "victoria_logs", "available": True, "rows": []},
            id="no-rows",
        ),
    ],
)
def test_victoria_logs_mapper_records_nothing_without_rows(output: dict[str, Any]) -> None:
    evidence: dict[str, Any] = {}
    _map_victoria_logs_query(evidence, output, {})
    assert "catalog_entries" not in evidence
