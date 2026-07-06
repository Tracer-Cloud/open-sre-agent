"""Unit tests for tool-owned evidence normalization."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.grafana.tools import (
    _normalize_grafana_alert_rules_evidence,
    _normalize_grafana_logs_evidence,
    _normalize_grafana_metrics_evidence,
    _normalize_grafana_service_names_evidence,
    _normalize_grafana_traces_evidence,
)


class TestGrafanaLogsNormalize:
    def test_extracts_logs_and_error_logs(self) -> None:
        output = {
            "logs": [{"message": "error occurred", "level": "ERROR"}],
            "error_logs": [{"message": "error occurred"}],
            "query": '{service_name="api"}',
            "service_name": "api",
        }
        result = _normalize_grafana_logs_evidence(output, {})
        assert result["grafana_logs"] == output["logs"]
        assert result["grafana_error_logs"] == output["error_logs"]
        assert result["grafana_logs_query"] == output["query"]
        assert result["grafana_logs_service"] == output["service_name"]

    def test_handles_empty_output(self) -> None:
        result = _normalize_grafana_logs_evidence({}, {})
        assert result["grafana_logs"] == []
        assert result["grafana_error_logs"] == []
        assert result["grafana_logs_query"] == ""
        assert result["grafana_logs_service"] == ""


class TestGrafanaMetricsNormalize:
    def test_extracts_metrics_and_metric_results(self) -> None:
        output: dict[str, Any] = {
            "metric_name": "pipeline_runs_total",
            "metrics": [{"value": 42}],
        }
        result = _normalize_grafana_metrics_evidence(output, {})
        assert result["grafana_metrics"] == output["metrics"]
        assert "grafana_metric_results" in result
        assert "pipeline_runs_total" in result["grafana_metric_results"]
        assert result["grafana_metric_results"]["pipeline_runs_total"] == output

    def test_uses_input_metric_name_fallback(self) -> None:
        output: dict[str, Any] = {"metrics": [{"value": 99}]}
        tool_input: dict[str, Any] = {"metric_name": "cpu_usage"}
        result = _normalize_grafana_metrics_evidence(output, tool_input)
        assert result["grafana_metrics"] == output["metrics"]
        assert "cpu_usage" in result["grafana_metric_results"]

    def test_skips_metric_results_without_name(self) -> None:
        output: dict[str, Any] = {"metrics": []}
        result = _normalize_grafana_metrics_evidence(output, {})
        assert result["grafana_metrics"] == []
        assert "grafana_metric_results" not in result


class TestGrafanaTracesNormalize:
    def test_extracts_traces_and_pipeline_spans(self) -> None:
        output: dict[str, Any] = {
            "traces": [{"trace_id": "abc123"}],
            "pipeline_spans": [{"span_id": "span1"}],
        }
        result = _normalize_grafana_traces_evidence(output, {})
        assert result["grafana_traces"] == output["traces"]
        assert result["grafana_pipeline_spans"] == output["pipeline_spans"]

    def test_handles_empty_output(self) -> None:
        result = _normalize_grafana_traces_evidence({}, {})
        assert result["grafana_traces"] == []
        assert result["grafana_pipeline_spans"] == []


class TestGrafanaAlertRulesNormalize:
    def test_extracts_rules(self) -> None:
        output: dict[str, Any] = {
            "rules": [{"name": "High CPU Alert", "state": "firing"}],
        }
        result = _normalize_grafana_alert_rules_evidence(output, {})
        assert result["grafana_alert_rules"] == output["rules"]

    def test_handles_empty_output(self) -> None:
        result = _normalize_grafana_alert_rules_evidence({}, {})
        assert result["grafana_alert_rules"] == []


class TestGrafanaServiceNamesNormalize:
    def test_extracts_service_names(self) -> None:
        output: dict[str, Any] = {
            "service_names": ["api", "worker", "web"],
        }
        result = _normalize_grafana_service_names_evidence(output, {})
        assert result["grafana_service_names"] == output["service_names"]

    def test_handles_empty_output(self) -> None:
        result = _normalize_grafana_service_names_evidence({}, {})
        assert result["grafana_service_names"] == []


class TestMergeToolEvidence:
    """Integration-level test of merge_tool_evidence with real RegisteredTool objects."""

    def test_merges_tool_owned_evidence_keys(self) -> None:
        from core.tool_framework.registered_tool import (
            _always_available,
            _extract_no_params,
            RegisteredTool,
        )
        from tools.investigation.stages.gather_evidence.tools import merge_tool_evidence

        tool = RegisteredTool(
            name="query_grafana_logs",
            description="Test tool",
            input_schema={"type": "object", "properties": {}, "required": []},
            source="grafana",
            run=lambda **kw: {},
            is_available=_always_available,
            extract_params=_extract_no_params,
            normalize_evidence=_normalize_grafana_logs_evidence,
        )
        evidence: dict[str, Any] = {}
        output = {
            "logs": [{"msg": "log1"}],
            "error_logs": [{"msg": "err1"}],
            "query": "test query",
            "service_name": "svc",
        }
        merge_tool_evidence(evidence, "query_grafana_logs", output, {}, [tool])
        assert evidence["grafana_logs"] == output["logs"]
        assert evidence["grafana_error_logs"] == output["error_logs"]
        assert evidence["grafana_logs_query"] == output["query"]
        assert evidence["grafana_logs_service"] == output["service_name"]
        assert evidence["query_grafana_logs"] == output

    def test_preserves_generic_tool_output_and_tool_outputs_list(self) -> None:
        from core.tool_framework.registered_tool import (
            _always_available,
            _extract_no_params,
            RegisteredTool,
        )
        from tools.investigation.stages.gather_evidence.tools import merge_tool_evidence

        tool = RegisteredTool(
            name="query_grafana_logs",
            description="Test tool",
            input_schema={"type": "object", "properties": {}, "required": []},
            source="grafana",
            run=lambda **kw: {},
            is_available=_always_available,
            extract_params=_extract_no_params,
            normalize_evidence=_normalize_grafana_logs_evidence,
        )
        evidence: dict[str, Any] = {}
        output = {"logs": []}
        merge_tool_evidence(evidence, "query_grafana_logs", output, {}, [tool])
        assert evidence["query_grafana_logs"] == output
        assert "tool_outputs" in evidence
        assert len(evidence["tool_outputs"]) == 1

    def test_noop_when_tool_not_in_list(self) -> None:
        from tools.investigation.stages.gather_evidence.tools import merge_tool_evidence

        evidence: dict[str, Any] = {}
        output = {"logs": [{"msg": "test"}]}
        merge_tool_evidence(evidence, "query_grafana_logs", output, {}, [])
        assert "grafana_logs" not in evidence
        assert evidence["query_grafana_logs"] == output

    def test_accumulates_metric_results_across_calls(self) -> None:
        from core.tool_framework.registered_tool import (
            _always_available,
            _extract_no_params,
            RegisteredTool,
        )
        from tools.investigation.stages.gather_evidence.tools import merge_tool_evidence

        tool = RegisteredTool(
            name="query_grafana_metrics",
            description="Test tool",
            input_schema={"type": "object", "properties": {}, "required": []},
            source="grafana",
            run=lambda **kw: {},
            is_available=_always_available,
            extract_params=_extract_no_params,
            normalize_evidence=_normalize_grafana_metrics_evidence,
        )
        evidence: dict[str, Any] = {}
        output1: dict[str, Any] = {"metric_name": "cpu", "metrics": [{"v": 1}]}
        output2: dict[str, Any] = {"metric_name": "mem", "metrics": [{"v": 2}]}
        merge_tool_evidence(evidence, "query_grafana_metrics", output1, {}, [tool])
        merge_tool_evidence(evidence, "query_grafana_metrics", output2, {}, [tool])
        assert "cpu" in evidence["grafana_metric_results"]
        assert "mem" in evidence["grafana_metric_results"]
        assert len(evidence["grafana_metric_results"]) == 2
