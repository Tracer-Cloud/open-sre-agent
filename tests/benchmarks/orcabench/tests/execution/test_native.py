from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.benchmarks.orcabench.config import GrafanaSettings, ModelSettings
from tests.benchmarks.orcabench.execution.environment import native_environment_values
from tests.benchmarks.orcabench.execution.native_connection import OrcaNativeConnections
from tests.benchmarks.orcabench.execution.native_report import NativeReportPolicy


def test_runner_bootstraps_project_platform_when_stdlib_loaded_first(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[5]
    script = f"""
import importlib.util
import sys
import sysconfig
from pathlib import Path

stdlib_path = Path(sysconfig.get_path("stdlib")) / "platform.py"
spec = importlib.util.spec_from_file_location("platform", stdlib_path)
assert spec is not None and spec.loader is not None
stdlib_platform = importlib.util.module_from_spec(spec)
sys.modules["platform"] = stdlib_platform
spec.loader.exec_module(stdlib_platform)

sys.path.insert(0, {str(repo_root)!r})
import tests.benchmarks.orcabench.execution.runner
import platform.terminal

assert hasattr(sys.modules["platform"], "__path__")
"""

    result = subprocess.run(
        (sys.executable, "-c", script),
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_native_connections_contain_telemetry_and_scoped_source_only(tmp_path: Path) -> None:
    source_root = tmp_path / "opentelemetry-demo"
    source_root.mkdir()
    window = {
        "since": "2026-04-21T11:00:00Z",
        "until": "2026-04-21T13:00:00Z",
    }
    resolved = OrcaNativeConnections(GrafanaSettings(), source_root).build(
        {"GRAFANA_URL": "http://frontend-proxy:8080/grafana/"},
        window,
    )

    grafana = resolved["grafana"]
    assert grafana["endpoint"] == "http://frontend-proxy:8080/grafana"
    assert grafana["api_key"] == "orca-basic-auth"
    assert grafana["username"] == "admin"
    assert grafana["password"] == "admin"
    assert grafana["verify_ssl"] is True
    assert grafana["connection_verified"] is True
    assert "default_metric_query" not in grafana
    assert grafana["_backend"].query_window == {
        "start": "2026-04-21T11:00:00Z",
        "end": "2026-04-21T13:00:00Z",
    }
    flattened_keys = set(resolved["grafana"])
    assert flattened_keys.isdisjoint({"query", "start", "end", "task", "report"})
    assert resolved["local_source"] == {
        "root_path": str(source_root),
        "connection_verified": True,
    }


def test_openrouter_environment_uses_provider_specific_model_names() -> None:
    values = native_environment_values(
        ModelSettings(
            harbor_model="openrouter/openrouter/free",
            provider="openrouter",
        )
    )

    assert values["LLM_PROVIDER"] == "openrouter"
    assert values["OPENROUTER_REASONING_MODEL"] == "openrouter/free"
    assert values["OPENROUTER_CLASSIFICATION_MODEL"] == "openrouter/free"
    assert values["OPENROUTER_TOOLCALL_MODEL"] == "openrouter/free"
    assert values["LLM_MAX_TOKENS"] == "16384"
    assert all(not name.startswith("OPENAI_") for name in values)


def test_native_report_policy_preserves_exact_utf8_bytes(tmp_path: Path) -> None:
    destination = tmp_path / "report.md"
    destination.touch(mode=0o666)
    report = "# Incident\n\nUnicode: café ∑\n"

    written = NativeReportPolicy().write(
        {"report": report, "root_cause_category": "configuration_error"},
        destination,
    )

    assert written == report.encode("utf-8")
    assert destination.read_bytes() == written


def test_native_report_policy_writes_empty_control_report(tmp_path: Path) -> None:
    destination = tmp_path / "report.md"
    destination.write_text("stale report", encoding="utf-8")

    written = NativeReportPolicy().write(
        {
            "report": "The investigated system is healthy.",
            "root_cause_category": "healthy",
        },
        destination,
    )

    assert written == b""
    assert destination.read_bytes() == b""
