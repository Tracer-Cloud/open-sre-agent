"""Tests for the VictoriaLogs evidence mapper.

The mapper lifts structured log rows returned by ``victoria_logs_query`` into
citeable report evidence. These tests pin both the happy path and the empty
result case so a broken CI producer cannot silently lose evidence.
"""

from __future__ import annotations

from tools.investigation.stages.gather_evidence.tools import merge_tool_evidence
from tools.registry import get_registered_tool


def test_registered_tool_carries_evidence_mapper() -> None:
    tool = get_registered_tool("victoria_logs_query")

    assert tool is not None
    assert tool.evidence_mapper is not None


def test_mapper_records_catalog_entry_for_rows() -> None:
    evidence: dict[str, object] = {}

    merge_tool_evidence(
        evidence,
        "victoria_logs_query",
        {"rows": [{"_msg": "boom"}, {"_msg": "retry"}], "total": 2},
        {},
    )

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert len(entries) == 1
    assert entries[0]["source"] == "victoria_logs_query"
    assert entries[0]["summary"] == "2 log entries"


def test_mapper_skips_catalog_entry_for_empty_rows() -> None:
    evidence: dict[str, object] = {}

    merge_tool_evidence(
        evidence,
        "victoria_logs_query",
        {"rows": [], "total": 0},
        {},
    )

    assert "catalog_entries" not in evidence
