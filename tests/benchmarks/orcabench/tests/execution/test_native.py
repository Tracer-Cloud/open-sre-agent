from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.benchmarks.orcabench.config import GrafanaSettings, ModelSettings
from tests.benchmarks.orcabench.execution.environment import native_environment_values
from tests.benchmarks.orcabench.execution.native_connection import OrcaNativeConnections
from tests.benchmarks.orcabench.execution.native_investigation import (
    NativeInvestigationIncompleteError,
    NativeInvestigationRunner,
)
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
    assert grafana["_backend"].allowed_query_window == {
        "start": "2026-04-21T11:00:00Z",
        "end": "2026-04-21T13:00:00Z",
    }
    flattened_keys = set(resolved["grafana"])
    assert flattened_keys.isdisjoint({"query", "start", "end", "task", "report"})
    assert resolved["local_source"] == {
        "root_path": str(source_root),
        "connection_verified": True,
    }


def test_terminus_parity_connections_allow_native_max_lookback(tmp_path: Path) -> None:
    source_root = tmp_path / "opentelemetry-demo"
    source_root.mkdir()
    window = {
        "since": "2026-04-21T11:00:00Z",
        "until": "2026-04-21T13:00:00Z",
    }

    resolved = OrcaNativeConnections(
        GrafanaSettings(),
        source_root,
        tool_capability_mode="terminus_parity",
    ).build(
        {"GRAFANA_URL": "http://frontend-proxy:8080/grafana/"},
        window,
    )

    assert resolved["grafana"]["_backend"].query_window == {
        "start": "2026-04-21T11:00:00Z",
        "end": "2026-04-21T13:00:00Z",
    }
    assert resolved["grafana"]["_backend"].allowed_query_window == {
        "start": "2026-04-14T13:00:00Z",
        "end": "2026-04-21T13:00:00Z",
    }


def test_openrouter_environment_uses_provider_specific_model_names() -> None:
    values = native_environment_values(
        ModelSettings(
            harbor_model="openrouter/nvidia/nemotron-3-super-120b-a12b:free",
            provider="openrouter",
        )
    )

    assert values["LLM_PROVIDER"] == "openrouter"
    expected_model = "nvidia/nemotron-3-super-120b-a12b:free"
    assert values["OPENROUTER_REASONING_MODEL"] == expected_model
    assert values["OPENROUTER_CLASSIFICATION_MODEL"] == expected_model
    assert values["OPENROUTER_TOOLCALL_MODEL"] == expected_model
    assert values["LLM_MAX_TOKENS"] == "16384"
    assert "OPENSRE_REASONING_EFFORT" not in values
    assert all(not name.startswith("OPENAI_") for name in values)


def test_nvidia_environment_uses_existing_native_provider_contract() -> None:
    values = native_environment_values(
        ModelSettings(harbor_model="nvidia/z-ai/glm-5.2", provider="nvidia")
    )

    assert values["LLM_PROVIDER"] == "nvidia"
    assert values["NVIDIA_REASONING_MODEL"] == "z-ai/glm-5.2"
    assert values["NVIDIA_CLASSIFICATION_MODEL"] == "z-ai/glm-5.2"
    assert values["NVIDIA_TOOLCALL_MODEL"] == "z-ai/glm-5.2"
    assert values["LLM_MAX_TOKENS"] == "16384"
    assert "OPENSRE_REASONING_EFFORT" not in values


def test_groq_environment_uses_existing_native_provider_contract() -> None:
    values = native_environment_values(
        ModelSettings(
            harbor_model="groq/openai/gpt-oss-120b",
            provider="groq",
        )
    )

    assert values["LLM_PROVIDER"] == "groq"
    assert values["GROQ_REASONING_MODEL"] == "openai/gpt-oss-120b"
    assert values["GROQ_CLASSIFICATION_MODEL"] == "openai/gpt-oss-120b"
    assert values["GROQ_TOOLCALL_MODEL"] == "openai/gpt-oss-120b"
    assert values["LLM_MAX_TOKENS"] == "16384"
    assert "OPENSRE_REASONING_EFFORT" not in values


def test_gemini_environment_uses_existing_native_provider_contract() -> None:
    values = native_environment_values(
        ModelSettings(
            harbor_model="gemini/gemini-3.5-flash-lite",
            provider="gemini",
        )
    )

    assert values["LLM_PROVIDER"] == "gemini"
    assert values["GEMINI_REASONING_MODEL"] == "gemini-3.5-flash-lite"
    assert values["GEMINI_CLASSIFICATION_MODEL"] == "gemini-3.5-flash-lite"
    assert values["GEMINI_TOOLCALL_MODEL"] == "gemini-3.5-flash-lite"
    assert values["LLM_MAX_TOKENS"] == "16384"
    assert "OPENSRE_REASONING_EFFORT" not in values


def test_openai_environment_includes_explicit_reasoning_effort_only() -> None:
    values = native_environment_values(
        ModelSettings(reasoning_effort="medium")
    )

    assert values["OPENSRE_REASONING_EFFORT"] == "medium"


def test_native_runner_uses_orca_guidance_agent_without_replacing_lifecycle(
    monkeypatch,
) -> None:
    import tools.investigation.capability as capability

    captured: dict[str, object] = {}

    def fake_run_investigation(alert, **kwargs):
        captured["alert"] = alert
        captured.update(kwargs)
        return {"root_cause": "test"}

    monkeypatch.setattr(capability, "run_investigation", fake_run_investigation)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.output.boundary.install_harness_ports",
        lambda: None,
    )
    alert = {
        "alert_source": "opensre_dataset",
        "_meta": {
            "orca_investigation_guidance": "There may be no root cause.",
            "orca_report_instructions": "Use Summary, Timeline, 5 Whys, and Remediation.",
        },
    }

    result = NativeInvestigationRunner().investigate(alert, {}, {"since": "start"})

    assert result == {"root_cause": "test"}
    assert captured["alert"] == alert
    assert captured["resolved_integrations"] == {}
    assert captured["incident_window"] == {"since": "start"}
    agent_class = captured["agent_class"]
    assert isinstance(agent_class, type)
    prompt = agent_class()._build_system_prompt({"raw_alert": alert})
    assert "## ORCA task guidance" in prompt
    assert "There may be no root cause." in prompt
    assert "## ORCA report contract" in prompt
    assert "Use Summary, Timeline, 5 Whys, and Remediation." in prompt
    from integrations.grafana.tools import query_grafana_metrics

    native_tool = query_grafana_metrics.__opensre_registered_tool__
    [orca_tool] = agent_class()._filter_tools([native_tool])
    assert "time_bounds" not in orca_tool.public_input_schema["properties"]
    assert "time_bounds" not in native_tool.public_input_schema["properties"]

    agent = agent_class()
    accepted, nudge = agent._should_accept_conclusion(
        evidence_count=3,
        iteration=4,
        final_text="Insufficient evidence.",
    )
    assert accepted is False
    assert nudge is not None
    assert "ORCA report contract" in nudge

    inline_heading_agent = agent_class()
    accepted, nudge = inline_heading_agent._should_accept_conclusion(
        evidence_count=3,
        iteration=5,
        final_text=(
            "The required headings are ## Summary, ## Timeline, ## 5 Whys, and "
            "## Remediation, but this is not a report."
        ),
    )
    assert accepted is False
    assert nudge is not None

    accepted, nudge = agent._should_accept_conclusion(
        evidence_count=3,
        iteration=5,
        final_text=(
            "## Summary\nIncident.\n\n"
            "## Timeline\n09:00 UTC.\n\n"
            "## 5 Whys\nWhy 1.\n\n"
            "## Remediation\nFix it."
        ),
    )
    assert (accepted, nudge) == (True, None)

    healthy_agent = agent_class()
    assert healthy_agent._should_accept_conclusion(
        evidence_count=3,
        iteration=4,
        final_text="Root cause category: healthy",
    ) == (True, None)

    non_healthy_agent = agent_class()
    accepted, nudge = non_healthy_agent._should_accept_conclusion(
        evidence_count=3,
        iteration=4,
        final_text="Root cause category: not healthy",
    )
    assert accepted is False
    assert nudge is not None


def test_native_payload_uses_agent_conclusion_as_orca_report() -> None:
    conclusion = (
        "## Summary\nCheckout failed.\n\n"
        "## Timeline\n09:00 UTC — failures began.\n\n"
        "## 5 Whys\nWhy 1: dependency failed.\n\n"
        "## Remediation\nRestore the dependency."
    )
    state = {
        "slack_message": "OpenSRE channel-formatted report",
        "problem_md": "checkout failures",
        "root_cause": "The payment dependency failed.",
        "root_cause_category": "dependency_failure",
        "agent_messages": [
            {"role": "assistant", "content": "Earlier status"},
            {"role": "assistant", "content": conclusion},
        ],
    }

    payload = NativeInvestigationRunner().build_payload(state)

    assert payload["report"] == conclusion
    assert payload["root_cause_category"] == "dependency_failure"


def test_native_payload_maps_terminal_healthy_disposition_to_empty_report(
    tmp_path: Path,
) -> None:
    state = {
        "slack_message": "OpenSRE channel-formatted report",
        "problem_md": "users are reporting site issues",
        "root_cause": "Unable to determine root cause",
        "root_cause_category": "unknown",
        "agent_messages": [
            {"role": "assistant", "content": "Earlier investigation status"},
            {"role": "assistant", "content": "Root cause category: healthy"},
        ],
    }

    payload = NativeInvestigationRunner().build_payload(state)
    destination = tmp_path / "report.md"
    destination.write_text("stale report", encoding="utf-8")
    written = NativeReportPolicy().write(payload, destination)

    assert payload["root_cause_category"] == "healthy"
    assert written == b""
    assert destination.read_bytes() == b""


def test_native_payload_rejects_iteration_cap_without_terminal_conclusion() -> None:
    state = {
        "slack_message": "Fallback report that must not be scored",
        "problem_md": "users are reporting site issues",
        "root_cause": "Unable to determine root cause",
        "root_cause_category": "unknown",
        "investigation_loop_count": 20,
        "investigation_iteration_cap": 20,
        "agent_messages": [
            {
                "role": "assistant",
                "content": "I will inspect one more source.",
                "tool_calls": [
                    {
                        "id": "last-call",
                        "function": {
                            "name": "list_local_source_tree",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {"role": "tool", "content": "source listing", "tool_call_id": "last-call"},
        ],
    }

    with pytest.raises(
        NativeInvestigationIncompleteError,
        match="iteration cap.*valid terminal ORCA conclusion",
    ):
        NativeInvestigationRunner().build_payload(state)


def test_native_payload_rejects_invalid_terminal_conclusion() -> None:
    state = {
        "slack_message": "Fallback report that must not be scored",
        "problem_md": "checkout failures",
        "root_cause": "Unable to determine root cause",
        "root_cause_category": "unknown",
        "agent_messages": [
            {"role": "assistant", "content": "Insufficient evidence."},
        ],
    }

    with pytest.raises(
        NativeInvestigationIncompleteError,
        match="valid terminal ORCA conclusion",
    ):
        NativeInvestigationRunner().build_payload(state)


def test_native_payload_propagates_llm_failure_instead_of_exporting_stale_text() -> None:
    state = {
        "slack_message": "Error report that must not be scored",
        "problem_md": "site issues",
        "root_cause": "Error: The LLM provider rejected the investigation request.",
        "root_cause_category": "Investigation Error",
        "causal_chain": [
            "LLM invoke failed: provider rejected malformed tool-call history"
        ],
        "agent_messages": [
            {
                "role": "assistant",
                "content": "Triage complete: provisional and unsupported incident",
                "tool_calls": [
                    {
                        "id": "bad-call",
                        "function": {
                            "name": "query_grafana_metrics",
                            "arguments": "{malformed",
                        },
                    }
                ],
            }
        ],
    }

    with pytest.raises(
        NativeInvestigationIncompleteError,
        match="LLM invocation failed.*provider rejected",
    ):
        NativeInvestigationRunner().build_payload(state)


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
