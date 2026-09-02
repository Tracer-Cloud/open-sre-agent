"""Evidence mapper coverage for the tracer_web tools (#5538)."""

from __future__ import annotations

from typing import Any

from integrations.tracer.tools.tracer_tasks_tool import _map_get_tracer_tasks


class TestMapTracerTasks:
    def test_records_count_and_failed(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_tracer_tasks(
            evidence,
            {
                "found": True,
                "total_tasks": 5,
                "failed_tasks": 2,
                "completed_tasks": 3,
                "tasks": [],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_tracer_tasks"
        assert entries[0]["summary"] == "5 task(s), 2 failed, 3 completed"

    def test_records_nothing_when_not_found(self) -> None:
        evidence: dict[str, Any] = {}
        _map_get_tracer_tasks(evidence, {"found": False}, {})
        assert "catalog_entries" not in evidence

    def test_records_nothing_when_no_tasks(self) -> None:
        evidence: dict[str, Any] = {}
        _map_get_tracer_tasks(evidence, {"found": True, "total_tasks": 0}, {})
        assert "catalog_entries" not in evidence
