from __future__ import annotations

from pathlib import Path

from core.agent_harness.session import Session
from surfaces.interactive_shell.utils.telemetry.config import PromptLogConfig
from surfaces.interactive_shell.utils.telemetry.recorder import LlmRunInfo, PromptRecorder


def test_prompt_recorder_start_respects_supported_turns(monkeypatch, tmp_path: Path) -> None:
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=False,
        redact=False,
        max_chars=100,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    session = Session()
    assert PromptRecorder.start(session=session, text="hello", turn_kind="slash") is None
    assert PromptRecorder.start(session=session, text="hello", turn_kind="agent") is not None


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
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    recorder = PromptRecorder.for_background_task(
        session=session, command="opensre investigate --service api", task_id="ab247135"
    )
    assert recorder is not None
    recorder.set_response("command failed (exit 1)\nboom")
    recorder.flush()
    assert captured
    assert captured[0]["cli_turn_kind"] == "background_task"
    assert captured[0]["$ai_trace_id"] == "ab247135"
    assert captured[0]["$ai_input"][0]["content"] == "opensre investigate --service api"
    assert captured[0]["$ai_output_choices"][0]["content"] == "command failed (exit 1)\nboom"


def test_prompt_recorder_for_background_task_disabled_returns_none(monkeypatch) -> None:
    cfg = PromptLogConfig(enabled=False)
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    session = Session()
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
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    session = Session()
    recorder = PromptRecorder.start(
        session=session,
        text="Bearer token-value-12345678901234567890",
        turn_kind="agent",
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
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.build_turn_integration_snapshot",
        lambda _session: {
            "connected_integrations": [],
            "connected_integrations_count": 0,
            "configured_integrations": [],
            "integration_snapshot_source": "runtime_config",
        },
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    recorder = PromptRecorder.start(
        session=session,
        text="hello",
        turn_kind="agent",
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
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.build_turn_integration_snapshot",
        lambda _session: {
            "connected_integrations": ["github"],
            "connected_integrations_count": 1,
            "configured_integrations": ["github"],
            "integration_snapshot_source": "runtime_config",
        },
    )
    session = Session()
    recorder = PromptRecorder.start(
        session=session,
        text="hello",
        turn_kind="agent",
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
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )

    def _boom(_resolved: dict[str, object]) -> list[object]:
        raise RuntimeError("tool registry blew up")

    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.integration_snapshot.get_available_tools",
        _boom,
    )

    session = Session()
    session.configured_integrations_known = True
    session.configured_integrations = ("datadog",)
    session.resolved_integrations_cache = {"datadog": {"api_key": "x", "app_key": "y"}}
    recorder = PromptRecorder.start(
        session=session,
        text="hello",
        turn_kind="agent",
    )
    assert recorder is not None
    recorder.set_response("world", LlmRunInfo(model="gpt-test", provider="openai", latency_ms=50))
    recorder.flush()
    assert captured
    assert captured[0]["$ai_model"] == "gpt-test"
    assert captured[0]["configured_integrations"] == ["datadog"]
    assert captured[0]["connected_integrations"] == []


def test_prompt_recorder_uses_no_conversational_agent_without_llm_run(
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
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.build_turn_integration_snapshot",
        lambda _session: {},
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    recorder = PromptRecorder.start(
        session=session,
        text="/help",
        turn_kind="agent",
    )
    assert recorder is not None
    recorder.set_response("slash /help (succeeded)")
    recorder.flush()
    assert captured[0]["$ai_model"] == "no_conversational_agent"
    assert captured[0]["$ai_provider"] == "no_conversational_agent"


def test_prompt_recorder_includes_investigation_id(monkeypatch, tmp_path: Path) -> None:
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
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.build_turn_integration_snapshot",
        lambda _session: {},
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    session.last_investigation_id = "inv-abc"
    recorder = PromptRecorder.start(
        session=session,
        text="/investigate generic",
        turn_kind="agent",
    )
    assert recorder is not None
    recorder.set_response(
        "slash /investigate generic (failed)\ninvestigation_failed (generic):\nboom"
    )
    recorder.flush()
    assert captured[0]["investigation_id"] == "inv-abc"


def test_prompt_recorder_omits_investigation_id_for_unrelated_turns(
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
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.build_turn_integration_snapshot",
        lambda _session: {},
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    session.last_investigation_id = "inv-stale"
    recorder = PromptRecorder.start(
        session=session,
        text="what integrations are configured?",
        turn_kind="agent",
    )
    assert recorder is not None
    recorder.set_response("github and datadog")
    recorder.flush()
    assert "investigation_id" not in captured[0]


def test_prompt_recorder_uses_prompt_fallback_when_response_empty(
    monkeypatch, tmp_path: Path
) -> None:
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.build_turn_integration_snapshot",
        lambda _session: {},
    )
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    session.record("slash", "/help", ok=True, response_text="slash /help (succeeded)")
    recorder = PromptRecorder.start(session=session, text="/help", turn_kind="agent")
    assert recorder is not None
    recorder.set_response("   ")
    recorder.flush()
    assert captured[0]["$ai_output_choices"][0]["content"] == "terminal turn handled: /help"


def test_prompt_recorder_background_task_uses_bound_investigation_id(
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
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.build_turn_integration_snapshot",
        lambda _session: {},
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    session.last_investigation_id = "inv-stale"
    recorder = PromptRecorder.for_background_task(
        session=session,
        command="opensre investigate --service api",
        task_id="task-123",
    )
    assert recorder is not None
    session.last_investigation_id = "inv-other"
    recorder.set_response("command completed (exit 0)")
    recorder.flush()
    investigation_id = captured[0]["investigation_id"]
    assert isinstance(investigation_id, str)
    assert investigation_id not in {"", "inv-stale", "inv-other"}


def test_prompt_recorder_uses_only_latest_slash_outcome(monkeypatch, tmp_path: Path) -> None:
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
        "surfaces.interactive_shell.utils.telemetry.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.build_turn_integration_snapshot",
        lambda _session: {},
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.utils.telemetry.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    session.record(
        "slash",
        "/modle",
        ok=False,
        response_text="Unknown command: /modle.",
        slash_outcome="unknown_command",
    )
    session.record("slash", "/help", ok=True, response_text="slash /help (succeeded)")
    recorder = PromptRecorder.start(
        session=session,
        text="what integrations are configured?",
        turn_kind="agent",
    )
    assert recorder is not None
    recorder.set_response("github and datadog")
    recorder.flush()
    assert "slash_outcome" not in captured[0]
