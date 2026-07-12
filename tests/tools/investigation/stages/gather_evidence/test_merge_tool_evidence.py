"""Tests for merge_tool_evidence — Datadog evidence key extraction."""

from __future__ import annotations

from typing import Any

from tools.investigation.stages.gather_evidence.tools import merge_tool_evidence


def _make_output(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "source": "datadog_investigate",
        "available": True,
        "logs": [{"message": "test log 1"}, {"message": "test log 2"}],
        "error_logs": [{"message": "OOMKilled pod-xyz"}],
        "total": 3,
        "query": "kube_namespace:data-pipeline status:error",
        "monitors": [{"name": "ETL health", "overall_state": "Alert"}],
        "events": [{"title": "Deploy v1.42.0"}],
        "fetch_duration_ms": {"logs": 150, "monitors": 200, "events": 100},
        "pod_name": "etl-worker-xyz",
        "container_name": "etl-container",
        "kube_namespace": "data-pipeline",
        "failed_pods": [{"pod_name": "etl-worker-xyz", "exit_code": "137"}],
        "errors": {},
    }
    defaults.update(overrides)
    return defaults


class TestMergeToolEvidenceDatadogAll:
    def test_sets_all_datadog_keys(self):
        evidence: dict[str, Any] = {}
        output = _make_output()
        merge_tool_evidence(evidence, "query_datadog_all", output, {"query": "status:error"})

        assert evidence["datadog_logs"] == output["logs"]
        assert evidence["datadog_error_logs"] == output["error_logs"]
        assert evidence["datadog_logs_query"] == output["query"]
        assert evidence["datadog_monitors"] == output["monitors"]
        assert evidence["datadog_events"] == output["events"]
        assert evidence["datadog_fetch_ms"] == output["fetch_duration_ms"]
        assert evidence["datadog_pod_name"] == output["pod_name"]
        assert evidence["datadog_container_name"] == output["container_name"]
        assert evidence["datadog_kube_namespace"] == output["kube_namespace"]
        assert evidence["datadog_failed_pods"] == output["failed_pods"]

    def test_preserves_raw_tool_output(self):
        evidence: dict[str, Any] = {}
        output = _make_output()
        merge_tool_evidence(evidence, "query_datadog_all", output, {"query": "status:error"})
        assert evidence["query_datadog_all"] is output

    def test_tool_outputs_appended(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "query_datadog_all", _make_output(), {})
        tool_outputs = evidence.get("tool_outputs", [])
        assert len(tool_outputs) == 1
        assert tool_outputs[0]["tool_name"] == "query_datadog_all"

    def test_updates_tool_outputs(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "query_grafana_logs", {"logs": []}, {})
        merge_tool_evidence(evidence, "query_datadog_all", _make_output(), {})
        assert len(evidence["tool_outputs"]) == 2

    def test_handles_missing_keys_gracefully(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "query_datadog_all", {"available": True}, {})
        assert evidence["datadog_logs"] == []
        assert evidence["datadog_error_logs"] == []
        assert evidence["datadog_logs_query"] == ""
        assert evidence["datadog_monitors"] == []
        assert evidence["datadog_events"] == []
        assert evidence["datadog_fetch_ms"] == {}
        assert evidence["datadog_pod_name"] == ""
        assert evidence["datadog_container_name"] == ""
        assert evidence["datadog_kube_namespace"] == ""
        assert evidence["datadog_failed_pods"] == []

    def test_unavailable_still_extracts_keys(self):
        evidence: dict[str, Any] = {}
        output = _make_output(available=False, logs=[], error_logs=[])
        merge_tool_evidence(evidence, "query_datadog_all", output, {})
        assert evidence["datadog_logs"] == []
        assert evidence["datadog_logs_query"] == "kube_namespace:data-pipeline status:error"


class TestMergeToolEvidenceDatadogLogs:
    def test_sets_log_keys(self):
        evidence: dict[str, Any] = {}
        output = {
            "source": "datadog_logs",
            "available": True,
            "logs": [{"message": "log1"}],
            "error_logs": [{"message": "err1"}],
            "total": 2,
            "query": "service:etl",
        }
        merge_tool_evidence(evidence, "query_datadog_logs", output, {"query": "service:etl"})
        assert evidence["datadog_logs"] == output["logs"]
        assert evidence["datadog_error_logs"] == output["error_logs"]
        assert evidence["datadog_logs_query"] == output["query"]

    def test_does_not_set_monitor_or_event_keys(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(
            evidence,
            "query_datadog_logs",
            {"logs": [], "error_logs": [], "query": ""},
            {},
        )
        assert "datadog_monitors" not in evidence
        assert "datadog_events" not in evidence
        assert "datadog_fetch_ms" not in evidence

    def test_handles_missing_keys(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "query_datadog_logs", {"available": True}, {})
        assert evidence["datadog_logs"] == []
        assert evidence["datadog_error_logs"] == []
        assert evidence["datadog_logs_query"] == ""


class TestMergeToolEvidenceDatadogMonitors:
    def test_sets_monitors_key(self):
        evidence: dict[str, Any] = {}
        output = {
            "source": "datadog_monitors",
            "available": True,
            "monitors": [{"name": "CPU Alert", "overall_state": "Alert"}],
            "total": 1,
            "query_filter": "tag:production",
        }
        merge_tool_evidence(evidence, "query_datadog_monitors", output, {})
        assert evidence["datadog_monitors"] == output["monitors"]

    def test_does_not_set_log_or_event_keys(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "query_datadog_monitors", {"monitors": []}, {})
        assert "datadog_logs" not in evidence
        assert "datadog_events" not in evidence

    def test_handles_missing_monitors_key(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "query_datadog_monitors", {"available": True}, {})
        assert evidence["datadog_monitors"] == []


class TestMergeToolEvidenceDatadogEvents:
    def test_sets_events_key(self):
        evidence: dict[str, Any] = {}
        output = {
            "source": "datadog_events",
            "available": True,
            "events": [{"title": "Deploy v1.42"}],
            "total": 1,
            "query": "service:etl",
        }
        merge_tool_evidence(evidence, "query_datadog_events", output, {})
        assert evidence["datadog_events"] == output["events"]

    def test_does_not_set_log_or_monitor_keys(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "query_datadog_events", {"events": []}, {})
        assert "datadog_logs" not in evidence
        assert "datadog_monitors" not in evidence

    def test_handles_missing_events_key(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "query_datadog_events", {"available": True}, {})
        assert evidence["datadog_events"] == []


class TestMergeToolEvidenceGeneral:
    def test_non_dict_output_returns_early(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "query_datadog_all", "just a string", {})
        assert "datadog_logs" not in evidence

    def test_none_output_returns_early(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "query_datadog_all", None, {})
        assert "datadog_logs" not in evidence

    def test_list_output_returns_early(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "query_datadog_all", [1, 2, 3], {})
        assert "datadog_logs" not in evidence

    def test_unknown_tool_does_not_crash(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "nonexistent_tool", {"key": "val"}, {})
        assert evidence["nonexistent_tool"] == {"key": "val"}
        assert "datadog_logs" not in evidence

    def test_regression_grafana_logs_still_works(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(
            evidence,
            "query_grafana_logs",
            {
                "logs": [{"message": "g1"}],
                "error_logs": [],
                "query": '{service="test"}',
                "service_name": "test-svc",
            },
            {},
        )
        assert evidence["grafana_logs"] == [{"message": "g1"}]
        assert evidence["grafana_logs_query"] == '{service="test"}'
        assert evidence["grafana_logs_service"] == "test-svc"

    def test_regression_grafana_metrics_still_works(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(
            evidence,
            "query_grafana_metrics",
            {"metrics": [{"metric": "cpu"}], "metric_name": "cpu_usage"},
            {"metric_name": "cpu_usage"},
        )
        assert evidence["grafana_metrics"] == [{"metric": "cpu"}]

    def test_regression_grafana_traces_still_works(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(
            evidence,
            "query_grafana_traces",
            {"traces": [{"trace_id": "abc"}], "pipeline_spans": [{"span": "s1"}]},
            {},
        )
        assert evidence["grafana_traces"] == [{"trace_id": "abc"}]
        assert evidence["grafana_pipeline_spans"] == [{"span": "s1"}]

    def test_regression_grafana_alert_rules_still_works(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(
            evidence,
            "query_grafana_alert_rules",
            {"rules": [{"name": "High CPU"}]},
            {},
        )
        assert evidence["grafana_alert_rules"] == [{"name": "High CPU"}]

    def test_regression_grafana_service_names_still_works(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(
            evidence,
            "query_grafana_service_names",
            {"service_names": ["svc-a", "svc-b"]},
            {},
        )
        assert evidence["grafana_service_names"] == ["svc-a", "svc-b"]

    def test_non_dict_early_return_still_appends_raw(self):
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "query_datadog_all", None, {"query": "test"})
        assert evidence["query_datadog_all"] is None
        assert len(evidence["tool_outputs"]) == 1
        assert evidence["tool_outputs"][0]["tool_name"] == "query_datadog_all"
