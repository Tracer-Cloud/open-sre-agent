"""Evidence mapper tests for the Grafana tools."""

from __future__ import annotations

from typing import Any

from integrations.grafana.tools import (
    _map_grafana_annotations,
)


def test_grafana_annotations_mapper_records_an_entry() -> None:
    # Arrange
    evidence: dict[str, Any] = {}
    output = {
        "source": "grafana_annotations",
        "available": True,
        "annotations": [
            {
                "time": "2023-01-01T10:00:00Z",
                "text": "Deploy v1.2.3",
                "tags": ["deploy", "production"],
            },
            {
                "time": "2023-01-01T11:00:00Z",
                "text": "Config change",
                "tags": ["config"],
            },
        ],
        "total": 2,
    }

    # Act
    _map_grafana_annotations(evidence, output, {})

    # Assert
    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "grafana_annotations"
    assert entries[0]["label"] == "Grafana Annotations"
    assert entries[0]["summary"] == "2 annotations"
    assert evidence["grafana_annotations"] == output["annotations"]


def test_grafana_annotations_mapper_records_nothing_when_empty() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "source": "grafana_annotations",
        "available": True,
        "annotations": [],
        "total": 0,
    }

    _map_grafana_annotations(evidence, output, {})

    assert evidence == {}