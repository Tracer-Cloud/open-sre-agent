from __future__ import annotations

from pathlib import Path

from app.cli.interactive_shell.prompt_logging.config import PromptLogConfig
from app.cli.interactive_shell.prompt_logging.recorder import LlmRunInfo, PromptRecorder
from app.cli.interactive_shell.runtime.session import ReplSession


def test_prompt_recorder_start_respects_supported_routes(monkeypatch, tmp_path: Path) -> None:
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=False,
        redact=False,
        max_chars=100,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.PromptLogConfig.load", lambda: cfg
    )
    session = ReplSession()
    assert PromptRecorder.start(session=session, text="hello", route_kind="slash") is None
    assert PromptRecorder.start(session=session, text="hello", route_kind="cli_help") is not None


def test_prompt_recorder_for_background_task_uses_task_id_as_trace(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = ReplSession()
    recorder = PromptRecorder.for_background_task(
        session=session, command="opensre investigate --service api", task_id="ab247135"
    )
    assert recorder is not None
    recorder.set_response("command failed (exit 1)\nboom")
    recorder.flush()
    assert captured
    assert captured[0]["cli_route_kind"] == "background_task"
    assert captured[0]["$ai_trace_id"] == "ab247135"
    assert captured[0]["$ai_input"][0]["content"] == "opensre investigate --service api"
    assert captured[0]["$ai_output_choices"][0]["content"] == "command failed (exit 1)\nboom"


def test_prompt_recorder_for_background_task_disabled_returns_none(monkeypatch) -> None:
    cfg = PromptLogConfig(enabled=False)
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.PromptLogConfig.load", lambda: cfg
    )
    session = ReplSession()
    assert PromptRecorder.for_background_task(session=session, command="x", task_id="t") is None


def test_prompt_recorder_flush_writes_and_redacts(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "prompt_log.jsonl"
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=True,
        posthog_enabled=False,
        redact=True,
        max_chars=1000,
        log_path=log_path,
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.PromptLogConfig.load", lambda: cfg
    )
    session = ReplSession()
    recorder = PromptRecorder.start(
        session=session,
        text="Bearer token-value-12345678901234567890",
        route_kind="cli_help",
    )
    assert recorder is not None
    recorder.set_response(
        "sk-ant-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        LlmRunInfo(model="m", provider="p", latency_ms=10),
    )
    recorder.flush()
    payload = log_path.read_text(encoding="utf-8")
    assert "Bearer [REDACTED]" in payload
    assert "[REDACTED:anthropic_key]" in payload


def test_prompt_recorder_sends_ai_generation(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.build_turn_integration_snapshot",
        lambda _session: {
            "connected_integrations": [],
            "connected_integrations_count": 0,
            "configured_integrations": [],
            "integration_snapshot_source": "runtime_config",
        },
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = ReplSession()
    recorder = PromptRecorder.start(
        session=session,
        text="hello",
        route_kind="handle_message_with_agent",
    )
    assert recorder is not None
    recorder.set_response("world", LlmRunInfo(model="gpt-test", provider="openai", latency_ms=50))
    recorder.flush()
    assert captured
    assert captured[0]["$ai_model"] == "gpt-test"
    assert captured[0]["$ai_input_tokens"] == 0
    assert captured[0]["connected_integrations"] == []
    assert captured[0]["connected_integrations_count"] == 0
    assert captured[0]["configured_integrations"] == []
    assert captured[0]["integration_snapshot_source"] == "runtime_config"


def test_prompt_recorder_sends_connected_integrations(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.build_turn_integration_snapshot",
        lambda _session: {
            "connected_integrations": ["github"],
            "connected_integrations_count": 1,
            "configured_integrations": ["github"],
            "integration_snapshot_source": "runtime_config",
        },
    )
    session = ReplSession()
    recorder = PromptRecorder.start(
        session=session,
        text="hello",
        route_kind="handle_message_with_agent",
    )
    assert recorder is not None
    recorder.set_response("world", LlmRunInfo(model="gpt-test", provider="openai", latency_ms=50))
    recorder.flush()
    assert captured[0]["connected_integrations"] == ["github"]
    assert captured[0]["connected_integrations_count"] == 1


def test_prompt_recorder_still_captures_when_tool_resolution_fails(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )

    def _boom(_resolved: dict[str, object]) -> list[object]:
        raise RuntimeError("tool registry blew up")

    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.integration_snapshot.get_available_tools",
        _boom,
    )

    session = ReplSession()
    session.configured_integrations_known = True
    session.configured_integrations = ("datadog",)
    session.resolved_integrations_cache = {"datadog": {"api_key": "x", "app_key": "y"}}
    recorder = PromptRecorder.start(
        session=session,
        text="hello",
        route_kind="handle_message_with_agent",
    )
    assert recorder is not None
    recorder.set_response("world", LlmRunInfo(model="gpt-test", provider="openai", latency_ms=50))
    recorder.flush()
    assert captured
    assert captured[0]["$ai_model"] == "gpt-test"
    assert captured[0]["configured_integrations"] == ["datadog"]
    assert captured[0]["connected_integrations"] == []
