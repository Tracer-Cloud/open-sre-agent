from __future__ import annotations

from pathlib import Path

from tests.benchmarks.orcabench.config import GrafanaSettings
from tests.benchmarks.orcabench.execution.native_connection import OrcaGrafanaConnection
from tests.benchmarks.orcabench.execution.native_report import NativeReportPolicy


def test_grafana_connection_contains_connection_data_only() -> None:
    resolved = OrcaGrafanaConnection(GrafanaSettings()).build(
        {"GRAFANA_URL": "http://frontend-proxy:8080/grafana/"}
    )

    assert resolved == {
        "grafana": {
            "endpoint": "http://frontend-proxy:8080/grafana",
            "api_key": "orca-basic-auth",
            "username": "admin",
            "password": "admin",
            "verify_ssl": True,
            "connection_verified": True,
        }
    }
    flattened_keys = set(resolved["grafana"])
    assert flattened_keys.isdisjoint({"query", "start", "end", "task", "report"})


def test_native_report_policy_preserves_exact_utf8_bytes(tmp_path: Path) -> None:
    destination = tmp_path / "report.md"
    destination.touch(mode=0o666)
    report = "# Incident\n\nUnicode: café ∑\n"

    written = NativeReportPolicy().write({"report": report}, destination)

    assert written == report.encode("utf-8")
    assert destination.read_bytes() == written
