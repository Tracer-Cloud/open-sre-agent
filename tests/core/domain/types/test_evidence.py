"""Repeated-call semantics for record_evidence_entry (Greptile P1, PR #5869)."""

from __future__ import annotations

from core.domain.types.evidence import record_evidence_entry


class TestRecordEvidenceEntry:
    def test_second_call_with_same_source_replaces_the_first(self) -> None:
        evidence: dict = {}

        record_evidence_entry(
            evidence, source="query_datadog_logs", label="Datadog Logs", summary="2 log line(s)"
        )
        record_evidence_entry(
            evidence, source="query_datadog_logs", label="Datadog Logs", summary="9 log line(s)"
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["summary"] == "9 log line(s)"

    def test_calls_with_different_sources_both_stay(self) -> None:
        evidence: dict = {}

        record_evidence_entry(
            evidence, source="query_datadog_logs", label="Datadog Logs", summary="2 log line(s)"
        )
        record_evidence_entry(
            evidence,
            source="query_datadog_monitors",
            label="Datadog Monitors",
            summary="1 monitor(s)",
        )

        entries = evidence["catalog_entries"]
        assert {e["source"] for e in entries} == {"query_datadog_logs", "query_datadog_monitors"}
