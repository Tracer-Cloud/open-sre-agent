from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.benchmarks.orcabench.config import (
    BenchmarkSettings,
    BuildManifest,
    RunnerSettings,
    RuntimeSettings,
)
from tests.benchmarks.orcabench.execution import runner
from tests.benchmarks.orcabench.execution.native_investigation import (
    NativeInvestigationIncompleteError,
    NativeInvestigationRunner,
)
from tests.benchmarks.orcabench.execution.runner import (
    _write_smoke_source_probe,
    _write_smoke_telemetry_probe,
)


class _Backend:
    def __init__(self) -> None:
        self.calls = 0

    def probe(self) -> dict[str, int]:
        self.calls += 1
        return {"trace_count": 1}


class _Writer:
    def __init__(self) -> None:
        self.writes: list[tuple[str, Any]] = []

    def write_json(self, path: str, payload: Any) -> None:
        self.writes.append((path, payload))


def test_smoke_profile_writes_telemetry_probe() -> None:
    backend = _Backend()
    writer = _Writer()

    _write_smoke_telemetry_probe(
        "smoke",
        {"grafana": {"_backend": backend}},
        writer,  # type: ignore[arg-type]
    )

    assert backend.calls == 1
    assert writer.writes == [("telemetry-probe.json", {"trace_count": 1})]


def test_benchmark_profile_does_not_probe_telemetry() -> None:
    backend = _Backend()
    writer = _Writer()

    _write_smoke_telemetry_probe(
        "benchmark",
        {"grafana": {"_backend": backend}},
        writer,  # type: ignore[arg-type]
    )

    assert backend.calls == 0
    assert writer.writes == []


def test_smoke_profile_probes_source_without_persisting_contents(tmp_path: Path) -> None:
    root = tmp_path / "opentelemetry-demo"
    root.mkdir()
    (root / "docker-compose.yml").write_text(
        "services:\n  checkout:\n    image: checkout\n",
        encoding="utf-8",
    )
    writer = _Writer()

    _write_smoke_source_probe(
        "smoke",
        {"local_source": {"root_path": str(root), "connection_verified": True}},
        writer,  # type: ignore[arg-type]
    )

    assert writer.writes == [
        (
            "source-probe.json",
            {
                "list_available": True,
                "entry_count": 1,
                "search_available": True,
                "match_count": 1,
                "read_available": True,
                "read_nonempty": True,
            },
        )
    ]
    assert "services:" not in str(writer.writes)


def test_benchmark_profile_does_not_probe_source(tmp_path: Path) -> None:
    root = tmp_path / "opentelemetry-demo"
    root.mkdir()
    writer = _Writer()

    _write_smoke_source_probe(
        "benchmark",
        {"local_source": {"root_path": str(root), "connection_verified": True}},
        writer,  # type: ignore[arg-type]
    )

    assert writer.writes == []


@pytest.mark.parametrize(
    ("failure_state", "error_match"),
    [
        pytest.param(
            {
                "root_cause": "Error: provider rejected the request.",
                "root_cause_category": "Investigation Error",
                "causal_chain": ["LLM invoke failed: invalid tool-call history"],
                "evidence_entries": [],
                "agent_messages": [
                    {"role": "assistant", "content": "unsupported provisional incident"}
                ],
            },
            "did not complete",
            id="llm-failure",
        ),
        pytest.param(
            {
                "root_cause": "Unable to determine root cause",
                "root_cause_category": "unknown",
                "evidence_entries": [],
                "investigation_loop_count": 20,
                "investigation_iteration_cap": 20,
                "agent_messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "last-call",
                                "function": {
                                    "name": "query_grafana_logs",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "content": "latest evidence",
                        "tool_call_id": "last-call",
                    },
                ],
            },
            "iteration cap",
            id="iteration-cap",
        ),
    ],
)
def test_runner_marks_incomplete_investigation_failed_without_writing_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_state: dict[str, Any],
    error_match: str,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    report_path = tmp_path / "report.md"
    report_path.write_bytes(b"")
    ready_path = tmp_path / "env-ready"
    ports_path = tmp_path / "env-ports"
    ready_path.touch()
    ports_path.touch()
    settings = RunnerSettings(
        benchmark=BenchmarkSettings(
            runtime=RuntimeSettings(
                report_path=report_path,
                source_root=tmp_path / "source",
                artifact_dir=artifact_dir,
                environment_ready_path=ready_path,
                environment_ports_path=ports_path,
            )
        ),
        build=BuildManifest(
            opensre_commit="1234567890abcdef",
            python_version="3.13.0",
            opensre_wheel="wheelhouse/opensre.whl",
            files_sha256={},
        ),
    )
    config_path = tmp_path / "runner-config.json"
    config_path.write_text(settings.to_json(), encoding="utf-8")
    instruction_path = tmp_path / "instruction.txt"
    instruction_path.write_text("test instruction", encoding="utf-8")
    investigation = SimpleNamespace(
        investigate=lambda *_args, **_kwargs: failure_state,
        build_payload=NativeInvestigationRunner().build_payload,
    )
    mode = SimpleNamespace(
        connections=SimpleNamespace(build=lambda *_args, **_kwargs: {}),
        investigation=investigation,
        report=SimpleNamespace(write=lambda *_args, **_kwargs: b"unexpected"),
    )
    task_context = SimpleNamespace(
        incident_window=lambda: {},
        investigation_alert=lambda: {},
    )
    monkeypatch.setattr(runner, "wait_for_path", lambda *_args: None)
    monkeypatch.setattr(runner, "configure_native_environment", lambda *_args: None)
    monkeypatch.setattr(runner, "build_mode", lambda *_args: mode)
    monkeypatch.setattr(runner, "parse_orca_task_context", lambda *_args: task_context)
    monkeypatch.setattr(runner, "check_grafana", lambda *_args: {"status": "ready"})

    with pytest.raises(NativeInvestigationIncompleteError, match=error_match):
        runner.run(config_path, instruction_path)

    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    error = json.loads((artifact_dir / "error.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["native_max_output_tokens"] == 16384
    assert error["category"] == "investigation_failed"
    assert error["exception_type"] == "NativeInvestigationIncompleteError"
    assert (artifact_dir / "state.json").is_file()
    assert not (artifact_dir / "payload.json").exists()
    assert not (artifact_dir / "report.md").exists()
    assert report_path.read_bytes() == b""
