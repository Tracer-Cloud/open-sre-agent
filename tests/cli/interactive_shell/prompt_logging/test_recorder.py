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
        "app.cli.interactive_shell.prompt_logging.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = ReplSession()
    recorder = PromptRecorder.start(session=session, text="hello", route_kind="cli_agent")
    assert recorder is not None
    recorder.set_response("world", LlmRunInfo(model="gpt-test", provider="openai", latency_ms=50))
    recorder.flush()
    assert captured
    assert captured[0]["$ai_model"] == "gpt-test"
    assert captured[0]["$ai_input_tokens"] == 0
