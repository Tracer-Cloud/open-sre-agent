"""Evidence-mapper coverage guard and characterization (Discussion #1079).

``merge_tool_evidence`` lifts a tool's raw output into the canonical report
keys the evidence catalog cites, using the mapper declared on the tool itself
(``@tool(evidence_mapper=...)``). A tool with no mapper contributes no citeable
evidence, so coverage silently drifts behind the tool registry as integrations
are added. These tests pin the mappings that exist and fail loudly when a new
tool joins the registry without a deliberate decision to map it or record it as
a known gap.
"""

from __future__ import annotations

from pathlib import Path

from core.domain.types.tools import ToolSurface
from tools.investigation.stages.gather_evidence.tools import merge_tool_evidence
from tools.registry import get_registered_tools

_BASELINE_PATH = Path(__file__).parent / "evidence_mapper_baseline.txt"


def _known_gaps() -> set[str]:
    lines = _BASELINE_PATH.read_text().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def _registered_and_mapped() -> tuple[set[str], set[str]]:
    """Return (all investigation tool names, names carrying an evidence mapper)."""
    tools = get_registered_tools(ToolSurface.INVESTIGATION)
    registered = {tool.name for tool in tools}
    mapped = {tool.name for tool in tools if tool.evidence_mapper is not None}
    return registered, mapped


class TestEvidenceMapperCoverage:
    """Every investigation tool is either mapped or a recorded known gap."""

    def test_no_new_unmapped_tool(self) -> None:
        # Arrange
        registered, mapped = _registered_and_mapped()

        # Act
        unmapped = registered - mapped

        # Assert: no investigation tool may be unmapped unless it is a recorded
        # known gap. A subset check (not equality) is deliberate — tool
        # discovery skips modules that fail to import (missing optional deps),
        # so the registered set legitimately shrinks in some environments; the
        # ratchet must not fail then. A new unmapped tool grows `unmapped` past
        # the baseline and fails here: declare `@tool(evidence_mapper=...)`, or
        # record the tool in the baseline as a deliberate gap.
        assert unmapped <= _known_gaps(), (
            "New investigation tool(s) with no evidence mapper: "
            f"{sorted(unmapped - _known_gaps())}. Declare @tool(evidence_mapper=...) "
            "or record the tool in evidence_mapper_baseline.txt."
        )

    def test_backfilled_tools_leave_the_baseline(self) -> None:
        # A tool cannot be both mapped and listed as a known gap. Forces a
        # backfill to drop its baseline line rather than leaving the list to rot.
        _registered, mapped = _registered_and_mapped()
        both = _known_gaps() & mapped
        assert not both, (
            f"Tool(s) both mapped and listed as a gap: {sorted(both)}. "
            "Remove them from evidence_mapper_baseline.txt."
        )


class TestGrafanaMappingCharacterization:
    """Pins the five canonical mappings that predate the registry refactor."""

    def test_logs_mapping(self) -> None:
        evidence: dict[str, object] = {}
        merge_tool_evidence(
            evidence,
            "query_grafana_logs",
            {"logs": [1], "error_logs": [2], "query": "q", "service_name": "svc"},
            {},
        )
        assert evidence["grafana_logs"] == [1]
        assert evidence["grafana_error_logs"] == [2]
        assert evidence["grafana_logs_query"] == "q"
        assert evidence["grafana_logs_service"] == "svc"

    def test_metrics_mapping_uses_tool_input_fallback(self) -> None:
        evidence: dict[str, object] = {}
        merge_tool_evidence(
            evidence,
            "query_grafana_metrics",
            {"metrics": [{"v": 1}]},
            {"metric_name": "cpu"},
        )
        assert evidence["grafana_metrics"] == [{"v": 1}]
        assert evidence["grafana_metric_results"] == {"cpu": {"metrics": [{"v": 1}]}}

    def test_traces_mapping(self) -> None:
        evidence: dict[str, object] = {}
        merge_tool_evidence(
            evidence,
            "query_grafana_traces",
            {"traces": ["t"], "pipeline_spans": ["s"]},
            {},
        )
        assert evidence["grafana_traces"] == ["t"]
        assert evidence["grafana_pipeline_spans"] == ["s"]

    def test_alert_rules_and_service_names_mapping(self) -> None:
        evidence: dict[str, object] = {}
        merge_tool_evidence(evidence, "query_grafana_alert_rules", {"rules": ["r"]}, {})
        merge_tool_evidence(evidence, "query_grafana_service_names", {"service_names": ["a"]}, {})
        assert evidence["grafana_alert_rules"] == ["r"]
        assert evidence["grafana_service_names"] == ["a"]

    def test_unmapped_tool_keeps_raw_output_only(self) -> None:
        evidence: dict[str, object] = {}
        merge_tool_evidence(evidence, "describe_rds_instance", {"status": "ok"}, {})
        # Raw blob survives; no canonical key is minted for an unmapped tool.
        assert evidence["describe_rds_instance"] == {"status": "ok"}
        assert not any(key.startswith("grafana_") for key in evidence)

    def test_non_dict_output_mints_no_canonical_key(self) -> None:
        evidence: dict[str, object] = {}
        merge_tool_evidence(evidence, "query_grafana_logs", "not-a-dict", {})
        assert evidence["query_grafana_logs"] == "not-a-dict"
        assert "grafana_logs" not in evidence

    def test_mapper_records_citeable_catalog_entry(self) -> None:
        # A mapper makes its output citeable by recording a catalog entry that
        # build_evidence_catalog turns into a display id.
        evidence: dict[str, object] = {}
        merge_tool_evidence(
            evidence,
            "query_grafana_metrics",
            {"metrics": [{"v": 1}], "metric_name": "cpu"},
            {},
        )
        entries = evidence["catalog_entries"]
        assert isinstance(entries, list)
        assert any(e["source"] == "grafana_metrics" for e in entries)


class TestJiraMapping:
    """Jira read tools expose their useful results as citeable evidence."""

    def test_issue_detail_mapping_records_issue_evidence(self) -> None:
        evidence: dict[str, object] = {}
        merge_tool_evidence(
            evidence,
            "jira_issue_detail",
            {"issue": {"issue_key": "OPS-42", "summary": "DB spike"}},
            {},
        )

        assert evidence["jira_issue_detail"] == {
            "issue": {"issue_key": "OPS-42", "summary": "DB spike"}
        }
        entries = evidence["catalog_entries"]
        assert isinstance(entries, list)
        assert any(entry["source"] == "jira_issue_detail" for entry in entries)

    def test_search_mapping_records_issue_list_evidence(self) -> None:
        evidence: dict[str, object] = {}
        merge_tool_evidence(
            evidence,
            "jira_search_issues",
            {"issues": [{"issue_key": "OPS-42", "summary": "DB spike"}], "total": 1},
            {},
        )

        entries = evidence["catalog_entries"]
        assert isinstance(entries, list)
        assert any(entry["source"] == "jira_search_issues" for entry in entries)
