from typing import Any

import pytest

from core.tool_framework.utils import tool_unavailable
from integrations.splunk.tools import _map_splunk_logs


def test_splunk_logs_mapper_records_an_entry_counting_logs_and_errors() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "source": "splunk_logs",
        "available": True,
        "logs": [
            {"message": "ERROR: database connection timeout", "timestamp": "2026-05-26T16:43:18Z"},
            {"message": "INFO: retrying connection", "timestamp": "2026-05-26T16:43:19Z"},
        ],
        "error_logs": [
            {"message": "ERROR: database connection timeout", "timestamp": "2026-05-26T16:43:18Z"}
        ],
        "total": 2,
    }

    _map_splunk_logs(evidence, output, {})

    assert evidence["catalog_entries"] == [
        {
            "source": "query_splunk_logs",
            "label": "Splunk Logs",
            "summary": "2 logs, 1 errors",
            "url": None,
            "snippet": None,
        }
    ]


def test_splunk_logs_mapper_reports_zero_errors_when_no_error_logs() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "source": "splunk_logs",
        "available": True,
        "logs": [
            {"message": "INFO: heartbeat ok", "timestamp": "2026-05-26T16:43:18Z"},
        ],
        "error_logs": [],
        "total": 1,
    }

    _map_splunk_logs(evidence, output, {})

    assert evidence["catalog_entries"] == [
        {
            "source": "query_splunk_logs",
            "label": "Splunk Logs",
            "summary": "1 logs, 0 errors",
            "url": None,
            "snippet": None,
        }
    ]


@pytest.mark.parametrize(
    "output",
    [
        pytest.param({}, id="empty-payload"),
        pytest.param(
            tool_unavailable("splunk_logs", "connection refused", logs=[], error_logs=[], total=0),
            id="unavailable-envelope",
        ),
        pytest.param(
            {"source": "splunk_logs", "available": True, "logs": [], "error_logs": []},
            id="no-logs",
        ),
    ],
)
def test_splunk_logs_mapper_records_nothing_without_logs(output: dict[str, Any]) -> None:
    evidence: dict[str, Any] = {}
    _map_splunk_logs(evidence, output, {})
    assert "catalog_entries" not in evidence
