from __future__ import annotations

from typing import Any, NoReturn

import pytest

from core.domain.stream import StreamEvent
from integrations.llm_cli.errors import CLIAuthenticationRequired
from surfaces.cli.error_mapping import reraise_cli_runtime_error
from surfaces.cli.investigation import run_investigation_cli, stream_investigation_cli
from surfaces.interactive_shell.utils.error_handling.errors import OpenSREError
from tools.investigation.capability import resolve_investigation_context


def test_resolve_investigation_context_prefers_cli_overrides() -> None:
    alert_name, pipeline_name, severity = resolve_investigation_context(
        raw_alert={
            "alert_name": "PayloadAlert",
            "pipeline_name": "payload_pipeline",
            "severity": "warning",
        },
        alert_name="CliAlert",
        pipeline_name="cli_pipeline",
        severity="critical",
    )

    assert alert_name == "CliAlert"
    assert pipeline_name == "cli_pipeline"
    assert severity == "critical"


def test_resolve_investigation_context_uses_raw_alert_without_pipeline_default() -> None:
    alert_name, pipeline_name, severity = resolve_investigation_context(
        raw_alert={
            "title": "CPU high",
            "commonLabels": {"service": "checkout", "severity": "critical"},
        },
        alert_name=None,
        pipeline_name=None,
        severity=None,
    )

    assert alert_name == "CPU high"
    assert pipeline_name == "checkout"
    assert severity == "critical"


def test_run_investigation_cli_passes_investigation_metadata_to_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_call(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "report": "r",
            "problem_md": "p",
            "root_cause": "c",
            "is_noise": False,
            "validity_score": 0.0,
        }

    monkeypatch.setattr("surfaces.cli.investigation.investigate.check_llm_settings", lambda: None)
    monkeypatch.setattr(
        "tools.investigation.capability.run_investigation_payload",
        fake_call,
    )
    run_investigation_cli(
        raw_alert={"description": "x"},
        investigation_metadata=("A", "B", "high"),
    )
    assert captured == {
        "raw_alert": {"description": "x"},
        "opensre_evaluate": False,
        "investigation_metadata": ("A", "B", "high"),
    }


def test_run_investigation_cli_shapes_agent_state(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_investigation(
        raw_alert: str | dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        captured["raw_alert"] = raw_alert
        captured.update(kwargs)
        return {
            "slack_message": "report body",
            "problem_md": "# problem",
            "root_cause": "bad deploy",
        }

    monkeypatch.setattr("surfaces.cli.investigation.investigate.check_llm_settings", lambda: None)
    monkeypatch.setattr(
        "tools.investigation.capability.run_investigation",
        fake_run_investigation,
    )

    result = run_investigation_cli(
        raw_alert={"alert_name": "PayloadAlert"},
    )

    assert captured == {
        "raw_alert": {"alert_name": "PayloadAlert"},
        "opensre_evaluate": False,
        "investigation_metadata": None,
    }
    assert result == {
        "report": "report body",
        "problem_md": "# problem",
        "root_cause": "bad deploy",
        "is_noise": False,
        "validity_score": 0.0,
    }


def test_run_investigation_cli_evaluate_reports_skip_when_no_rubric(monkeypatch) -> None:
    def fake_run(
        raw_alert: str | dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        return {
            "slack_message": "r",
            "problem_md": "p",
            "root_cause": "c",
            "opensre_evaluate": True,
            "opensre_eval_rubric": "",
            "opensre_llm_eval": {},
        }

    monkeypatch.setattr("surfaces.cli.investigation.investigate.check_llm_settings", lambda: None)
    monkeypatch.setattr("tools.investigation.capability.run_investigation", fake_run)

    result = run_investigation_cli(
        raw_alert={"alert_name": "A"},
        opensre_evaluate=True,
    )
    assert result["opensre_llm_eval"]["skipped"] is True
    assert "No scoring_points" in result["opensre_llm_eval"]["reason"]


def test_parse_args_evaluate_flag() -> None:
    from surfaces.cli.args import parse_args

    assert parse_args(["--input", "a.json"]).evaluate is False
    assert parse_args(["--input", "a.json", "--evaluate"]).evaluate is True


def test_run_investigation_cli_fails_fast_for_missing_llm_auth(monkeypatch, tmp_path) -> None:
    from config.llm_auth.credentials import CredentialStatus
    from platform.common.errors import OpenSREError

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr(
        "config.llm_auth.credentials.status",
        lambda provider: CredentialStatus(
            provider=provider,
            configured=False,
            source="none",
            verified=False,
            stale=False,
            detail="OPENAI_API_KEY is not set",
        ),
    )
    monkeypatch.setattr(
        "tools.investigation.capability.run_investigation_payload",
        lambda *_args, **_kwargs: pytest.fail("investigation should not start"),
    )

    with pytest.raises(OpenSREError, match="credentials are missing"):
        run_investigation_cli(raw_alert={"alert_name": "PayloadAlert"})


def test_stream_investigation_cli_raises_queued_exception_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_astream_investigation(*args: object, **kwargs: object):
        yield StreamEvent("metadata", data={"run_id": "run-123"})
        raise RuntimeError("stream failed")

    monkeypatch.setattr("surfaces.cli.investigation.investigate.check_llm_settings", lambda: None)
    monkeypatch.setattr(
        "tools.investigation.capability.astream_investigation",
        fake_astream_investigation,
    )

    events = stream_investigation_cli(raw_alert={"alert_name": "PayloadAlert"})

    first = next(events)
    assert first.event_type == "metadata"
    with pytest.raises(RuntimeError, match="stream failed"):
        next(events)


def test_stream_investigation_cli_closes_cleanly_on_generator_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing the generator must not hang and must clean up the background thread."""
    import asyncio
    import time

    async def fake_astream_investigation(*args: object, **kwargs: object):
        yield StreamEvent("metadata", data={"run_id": "run-123"})
        # Simulate a long-running stream
        await asyncio.sleep(1000)

    monkeypatch.setattr("surfaces.cli.investigation.investigate.check_llm_settings", lambda: None)
    monkeypatch.setattr(
        "tools.investigation.capability.astream_investigation",
        fake_astream_investigation,
    )

    events = stream_investigation_cli(raw_alert={"alert_name": "PayloadAlert"})
    first = next(events)
    assert first.event_type == "metadata"

    # Without eager pump cancellation, thread.join() would block for the full timeout (~5s).
    t0 = time.monotonic()
    events.close()
    assert time.monotonic() - t0 < 2.0


def test_run_investigation_cli_maps_cli_auth_to_opensre_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(raw_alert: str | dict[str, object], **_kwargs: object) -> NoReturn:
        raise CLIAuthenticationRequired(
            provider="cursor",
            auth_hint="Run: agent login.",
            detail="Not logged in.",
        )

    monkeypatch.setattr("surfaces.cli.investigation.investigate.check_llm_settings", lambda: None)
    monkeypatch.setattr("tools.investigation.capability.run_investigation", boom)

    with pytest.raises(OpenSREError, match="not authenticated") as exc_info:
        run_investigation_cli(raw_alert={"alert_name": "PayloadAlert"})
    assert exc_info.value.suggestion is not None
    assert "agent login" in exc_info.value.suggestion


def test_stream_investigation_cli_maps_cli_auth_to_opensre_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_astream_investigation(*args: object, **kwargs: object):
        yield StreamEvent("metadata", data={"run_id": "run-123"})
        raise CLIAuthenticationRequired(
            provider="cursor",
            auth_hint="Run: agent login.",
            detail="Not logged in.",
        )

    monkeypatch.setattr("surfaces.cli.investigation.investigate.check_llm_settings", lambda: None)
    monkeypatch.setattr(
        "tools.investigation.capability.astream_investigation",
        fake_astream_investigation,
    )

    events = stream_investigation_cli(raw_alert={"alert_name": "PayloadAlert"})
    assert next(events).event_type == "metadata"
    with pytest.raises(OpenSREError, match="not authenticated"):
        next(events)


def test_reraise_cli_runtime_error_maps_cli_auth() -> None:
    exc = CLIAuthenticationRequired(
        provider="opencode",
        auth_hint="Run: opencode auth login",
        detail="not logged in",
    )

    with pytest.raises(OpenSREError) as raised:
        reraise_cli_runtime_error(exc)

    assert str(raised.value) == "opencode CLI is not authenticated."
    assert raised.value.suggestion == "Run: opencode auth login (not logged in)"


def test_reraise_cli_runtime_error_maps_cli_not_found() -> None:
    exc = RuntimeError("codex CLI not found on PATH")

    with pytest.raises(OpenSREError) as raised:
        reraise_cli_runtime_error(exc)

    assert str(raised.value) == "CLI tool is not installed or not found."
    assert raised.value.suggestion == "codex CLI not found on PATH"


def test_reraise_cli_runtime_error_reraises_unknown_runtime_error() -> None:
    exc = RuntimeError("some unrelated runtime failure")

    with pytest.raises(RuntimeError, match="some unrelated runtime failure"):
        reraise_cli_runtime_error(exc)
