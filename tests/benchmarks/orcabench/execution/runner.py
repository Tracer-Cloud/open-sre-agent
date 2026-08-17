"""In-container composition root for one native OpenSRE ORCA investigation."""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()

from tests.benchmarks.orcabench.artifacts import (
    ArtifactWriter,
    ErrorRecord,
    ModelCallAttemptEvent,
    RunManifest,
    RunStatus,
    RunSummary,
    UsageEvent,
    sha256_bytes,
)
from tests.benchmarks.orcabench.artifacts.redaction import Redactor
from tests.benchmarks.orcabench.config import RunnerSettings
from tests.benchmarks.orcabench.execution.environment import (
    configure_native_environment,
    wait_for_path,
)
from tests.benchmarks.orcabench.execution.health import check_grafana
from tests.benchmarks.orcabench.execution.modes import build_mode
from tests.benchmarks.orcabench.execution.task_context import parse_orca_task_context


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--instruction", type=Path, required=True)
    return parser


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _error_category(exc: BaseException) -> str:
    """Classify failures at the integration boundary without hiding their type."""
    if isinstance(exc, TimeoutError):
        return "environment_not_ready"
    if isinstance(exc, (ValueError, KeyError)):
        return "payload_or_configuration_invalid"
    if isinstance(exc, OSError):
        return "environment_io_failed"
    return "investigation_failed"


def _write_smoke_telemetry_probe(
    profile: str,
    integrations: dict[str, Any],
    writer: ArtifactWriter,
) -> None:
    """Persist deterministic adapter evidence only for non-scored smoke runs."""
    if profile != "smoke":
        return
    telemetry_backend = integrations.get("grafana", {}).get("_backend")
    if telemetry_backend is not None:
        writer.write_json("telemetry-probe.json", telemetry_backend.probe())


def _write_smoke_source_probe(
    profile: str,
    integrations: dict[str, Any],
    writer: ArtifactWriter,
) -> None:
    """Exercise scoped source tools in smoke runs without persisting source text."""
    if profile != "smoke":
        return
    source = integrations.get("local_source")
    if not isinstance(source, dict):
        return

    from tools.system.local_source import (
        list_local_source_tree,
        read_local_source_file,
        search_local_source,
    )

    root_path = str(source.get("root_path") or "")
    listing = list_local_source_tree(depth=1, limit=5, root_path=root_path)
    search = search_local_source(
        query="services:",
        path="docker-compose.yml",
        limit=1,
        root_path=root_path,
    )
    read = read_local_source_file(
        path="docker-compose.yml",
        start_line=1,
        end_line=1,
        root_path=root_path,
    )
    writer.write_json(
        "source-probe.json",
        {
            "list_available": listing.get("available") is True,
            "entry_count": len(listing.get("entries") or []),
            "search_available": search.get("available") is True,
            "match_count": len(search.get("matches") or []),
            "read_available": read.get("available") is True,
            "read_nonempty": bool(read.get("content")),
        },
    )


def _manifest(
    settings: RunnerSettings,
    instruction: bytes,
    started_at: datetime,
) -> RunManifest:
    model = settings.benchmark.model
    return RunManifest(
        profile=settings.benchmark.profile,
        tool_capability_mode=settings.benchmark.tool_capability_mode,
        status=RunStatus.RUNNING,
        integration_version=settings.integration_version,
        opensre_commit=settings.build.opensre_commit,
        dirty_files=settings.build.dirty_files,
        python_version=sys.version.split()[0],
        model=model.opensre_model,
        model_provider=model.provider,
        model_transport=model.transport,
        reasoning_effort=model.reasoning_effort,
        temperature=model.temperature,
        native_max_output_tokens=model.max_tokens,
        instruction_sha256=sha256_bytes(instruction),
        started_at=started_at,
    )


def run(config_path: Path, instruction_path: Path) -> int:
    """Run one configured native investigation and persist its complete artifacts."""
    settings = RunnerSettings.from_path(config_path)
    runtime = settings.benchmark.runtime
    secret_names = dict.fromkeys(
        settings.benchmark.model.required_environment_names
        + settings.benchmark.verifier.required_environment_names
    )
    known_secrets = tuple(
        value for name in secret_names if (value := os.environ.get(name, ""))
    )
    writer = ArtifactWriter(runtime.artifact_dir, Redactor(known_secrets))

    instruction_bytes = instruction_path.read_bytes()
    instruction = instruction_bytes.decode("utf-8")
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    manifest = _manifest(settings, instruction_bytes, started_at)
    writer.write_json("manifest.json", manifest)
    writer.write_json("config.json", settings)
    writer.write_bytes("instruction.txt", instruction_bytes)

    usage_events: list[UsageEvent] = []
    model_call_attempts: list[ModelCallAttemptEvent] = []
    state: dict[str, Any] | None = None

    try:
        wait_for_path(
            runtime.environment_ready_path,
            runtime.readiness_timeout_seconds,
        )
        if not runtime.environment_ports_path.is_file():
            raise RuntimeError(
                f"ORCA environment ports file is missing: {runtime.environment_ports_path}"
            )

        configure_native_environment(settings)
        grafana_endpoint = os.environ.get("GRAFANA_URL", "").strip()
        components = build_mode(settings)
        task_context = parse_orca_task_context(instruction)
        incident_window = task_context.incident_window()
        integrations = components.connections.build(dict(os.environ), incident_window)
        health = check_grafana(
            grafana_endpoint,
            settings.benchmark.grafana,
            runtime.grafana_timeout_seconds,
        )
        writer.write_json("health.json", health)
        _write_smoke_telemetry_probe(
            settings.benchmark.profile,
            integrations,
            writer,
        )
        _write_smoke_source_probe(
            settings.benchmark.profile,
            integrations,
            writer,
        )

        from core.llm.shared.usage import (
            ModelCallAttempt,
            ModelCallUsage,
            set_detailed_usage_hook,
            set_model_call_attempt_hook,
        )

        def collect_usage(event: ModelCallUsage) -> None:
            usage_events.append(
                UsageEvent(
                    sequence=len(usage_events) + 1,
                    requested_model=event.requested_model,
                    response_model=event.response_model,
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                    cache_read_tokens=event.cache_read_tokens,
                    cache_creation_tokens=event.cache_creation_tokens,
                    api_type=event.api_type,
                    max_output_tokens=event.max_output_tokens,
                    temperature=event.temperature,
                    reasoning_effort=event.reasoning_effort,
                )
            )

        def collect_model_call_attempt(event: ModelCallAttempt) -> None:
            model_call_attempts.append(
                ModelCallAttemptEvent(
                    sequence=len(model_call_attempts) + 1,
                    requested_model=event.requested_model,
                    api_type=event.api_type,
                    attempt=event.attempt,
                    status=event.status,
                    duration_seconds=event.duration_seconds,
                    response_model=event.response_model,
                    response_id=event.response_id,
                    error_type=event.error_type,
                )
            )

        set_detailed_usage_hook(collect_usage)
        set_model_call_attempt_hook(collect_model_call_attempt)
        try:
            state = components.investigation.investigate(
                task_context.investigation_alert(),
                integrations,
                incident_window,
            )
            writer.write_json("state.json", state)
            writer.write_jsonl("evidence.jsonl", list(state.get("evidence_entries") or []))
            payload = components.investigation.build_payload(state)
        finally:
            set_model_call_attempt_hook(None)
            set_detailed_usage_hook(None)

        writer.write_json("payload.json", payload)
        report_bytes = components.report.write(payload, runtime.report_path)
        writer.write_bytes("report.md", report_bytes)
        writer.write_jsonl("usage.jsonl", usage_events)
        writer.write_jsonl("model-calls.jsonl", model_call_attempts)

        finished_at = _utc_now()
        report_sha256 = sha256_bytes(report_bytes)
        completed = manifest.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "report_sha256": report_sha256,
                "finished_at": finished_at,
                "duration_seconds": time.monotonic() - started_monotonic,
                "llm_calls": len(usage_events),
                "input_tokens": sum(event.input_tokens for event in usage_events),
                "output_tokens": sum(event.output_tokens for event in usage_events),
                "cache_read_tokens": sum(event.cache_read_tokens or 0 for event in usage_events),
                "cache_creation_tokens": sum(
                    event.cache_creation_tokens or 0 for event in usage_events
                ),
                "returned_models": tuple(
                    dict.fromkeys(
                        event.response_model
                        for event in usage_events
                        if event.response_model
                    )
                ),
            }
        )
        writer.write_json("manifest.json", completed)
        writer.write_json(
            "summary.json",
            RunSummary(
                llm_calls=completed.llm_calls,
                input_tokens=completed.input_tokens,
                output_tokens=completed.output_tokens,
                cache_read_tokens=completed.cache_read_tokens,
                cache_creation_tokens=completed.cache_creation_tokens,
                report_sha256=report_sha256,
            ),
        )
        return 0
    except Exception as exc:
        if state is not None:
            writer.write_json("state.json", state)
            writer.write_jsonl("evidence.jsonl", list(state.get("evidence_entries") or []))
        writer.write_jsonl("usage.jsonl", usage_events)
        writer.write_jsonl("model-calls.jsonl", model_call_attempts)
        writer.write_json(
            "error.json",
            ErrorRecord(
                category=_error_category(exc),
                exception_type=type(exc).__name__,
                message=str(exc),
                traceback="".join(traceback.format_exception(exc)),
            ),
        )
        failed = manifest.model_copy(
            update={
                "status": RunStatus.FAILED,
                "finished_at": _utc_now(),
                "duration_seconds": time.monotonic() - started_monotonic,
                "llm_calls": len(usage_events),
                "input_tokens": sum(event.input_tokens for event in usage_events),
                "output_tokens": sum(event.output_tokens for event in usage_events),
                "cache_read_tokens": sum(event.cache_read_tokens or 0 for event in usage_events),
                "cache_creation_tokens": sum(
                    event.cache_creation_tokens or 0 for event in usage_events
                ),
                "returned_models": tuple(
                    dict.fromkeys(
                        event.response_model
                        for event in usage_events
                        if event.response_model
                    )
                ),
            }
        )
        writer.write_json("manifest.json", failed)
        raise


def main() -> int:
    """CLI entry point used by the installed Harbor agent."""
    args = _parser().parse_args()
    return run(args.config, args.instruction)


if __name__ == "__main__":
    raise SystemExit(main())
