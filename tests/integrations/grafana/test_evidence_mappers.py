from typing import Any

import pytest

from core.tool_framework.utils import tool_unavailable
from integrations.grafana.tools import _map_grafana_annotations


def test_annotations_mapper_records_an_entry_counting_annotations() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "source": "grafana_annotations",
        "available": True,
        "annotations": [
            {"text": "deploy v2.4.1", "time": "2026-05-26T16:30:00Z", "tags": ["deploy"]},
            {"text": "config update", "time": "2026-05-26T16:35:00Z", "tags": ["config"]},
        ],
        "total": 2,
    }

    _map_grafana_annotations(evidence, output, {})

    assert evidence["grafana_annotations"] == output["annotations"]
    assert evidence["catalog_entries"] == [
        {
            "source": "query_grafana_annotations",
            "label": "Grafana Annotations",
            "summary": "2 annotations",
            "url": None,
            "snippet": None,
        }
    ]


@pytest.mark.parametrize(
    "output",
    [
        pytest.param({}, id="empty-payload"),
        pytest.param(
            tool_unavailable("grafana_annotations", "not configured", annotations=[]),
            id="unavailable-envelope",
        ),
        pytest.param(
            {"source": "grafana_annotations", "available": True, "annotations": []},
            id="no-annotations",
        ),
    ],
)
def test_annotations_mapper_records_nothing_without_annotations(output: dict[str, Any]) -> None:
    evidence: dict[str, Any] = {}
    _map_grafana_annotations(evidence, output, {})
    assert "catalog_entries" not in evidence
