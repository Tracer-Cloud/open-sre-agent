"""Evidence mapper coverage for the datadog tools (#5539)."""

from __future__ import annotations

from integrations.datadog.tools import (
    _map_get_pods_on_node,
    _map_query_datadog_all,
    _map_query_datadog_events,
    _map_query_datadog_logs,
    _map_query_datadog_metrics,
    _map_query_datadog_monitors,
)


class TestMapQueryDatadogAll:
    def test_records_entry_counting_each_payload(self) -> None:
        evidence: dict = {}

        _map_query_datadog_all(
            evidence,
            {
                "logs": [{"message": "a"}, {"message": "b"}],
                "monitors": [{"id": 1}],
                "events": [],
                "failed_pods": [{"pod_name": "p"}],
            },
            {},
        )

        entry = evidence["catalog_entries"][0]
        assert entry["source"] == "query_datadog_all"
        assert entry["summary"] == "2 log line(s), 1 monitor(s), 1 failed pod(s)"

    def test_records_nothing_when_every_payload_empty(self) -> None:
        evidence: dict = {}

        _map_query_datadog_all(
            evidence, {"logs": [], "monitors": [], "events": [], "failed_pods": []}, {}
        )

        assert "catalog_entries" not in evidence


class TestMapQueryDatadogEvents:
    def test_records_entry_when_events_present(self) -> None:
        evidence: dict = {}

        _map_query_datadog_events(evidence, {"events": [{"title": "deploy"}]}, {})

        entry = evidence["catalog_entries"][0]
        assert entry["source"] == "query_datadog_events"
        assert entry["summary"] == "1 event(s)"

    def test_records_nothing_when_no_events(self) -> None:
        evidence: dict = {}

        _map_query_datadog_events(evidence, {"events": []}, {})

        assert "catalog_entries" not in evidence


class TestMapQueryDatadogLogs:
    def test_records_entry_when_logs_present(self) -> None:
        evidence: dict = {}

        _map_query_datadog_logs(evidence, {"logs": [{"message": "ok"}, {"message": "boom"}]}, {})

        entry = evidence["catalog_entries"][0]
        assert entry["source"] == "query_datadog_logs"
        assert entry["summary"] == "2 log line(s)"

    def test_records_nothing_when_no_logs(self) -> None:
        evidence: dict = {}

        _map_query_datadog_logs(evidence, {"logs": []}, {})

        assert "catalog_entries" not in evidence


class TestMapQueryDatadogMonitors:
    def test_records_entry_when_monitors_present(self) -> None:
        evidence: dict = {}

        _map_query_datadog_monitors(evidence, {"monitors": [{"id": 1}, {"id": 2}]}, {})

        entry = evidence["catalog_entries"][0]
        assert entry["source"] == "query_datadog_monitors"
        assert entry["summary"] == "2 monitor(s)"

    def test_records_nothing_when_no_monitors(self) -> None:
        evidence: dict = {}

        _map_query_datadog_monitors(evidence, {"monitors": []}, {})

        assert "catalog_entries" not in evidence


class TestMapQueryDatadogMetrics:
    def test_stub_output_records_nothing(self) -> None:
        """The tool is not implemented and always returns no series, so the
        mapper contributes no evidence until a real Metrics API call lands."""
        evidence: dict = {}

        _map_query_datadog_metrics(evidence, {"available": False, "metrics": []}, {})

        assert "catalog_entries" not in evidence

    def test_records_entry_once_series_are_returned(self) -> None:
        evidence: dict = {}

        _map_query_datadog_metrics(evidence, {"metrics": [{"points": []}, {"points": []}]}, {})

        entry = evidence["catalog_entries"][0]
        assert entry["source"] == "query_datadog_metrics"
        assert entry["summary"] == "2 series"


class TestMapGetPodsOnNode:
    def test_records_entry_when_pods_present(self) -> None:
        evidence: dict = {}

        _map_get_pods_on_node(evidence, {"pods": ["pod-a", "pod-b"]}, {})

        entry = evidence["catalog_entries"][0]
        assert entry["source"] == "get_pods_on_node"
        assert entry["summary"] == "2 pod(s)"

    def test_records_nothing_when_no_pods(self) -> None:
        evidence: dict = {}

        _map_get_pods_on_node(evidence, {"pods": []}, {})

        assert "catalog_entries" not in evidence
