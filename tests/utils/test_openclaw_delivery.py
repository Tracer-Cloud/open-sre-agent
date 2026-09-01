from __future__ import annotations

import logging
from typing import Any

import pytest

from integrations.openclaw.delivery import MISSING_CONVERSATION_ID, send_openclaw_report


def _state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "alert_name": "Checkout API error rate spike",
        "root_cause": "A bad deploy introduced 5xx errors.",
        "remediation_steps": ["Roll back the deploy", "Verify health checks"],
        "validity_score": 0.92,
        "channel_contexts": {"openclaw": {}},
    }
    base.update(overrides)
    return base


def _creds(**overrides: Any) -> dict[str, Any]:
    base = {
        "mode": "streamable-http",
        "url": "https://openclaw.example.com/mcp",
        "auth_token": "tok",
        "command": "",
        "args": [],
    }
    base.update(overrides)
    return base


def test_send_openclaw_report_without_conversation_id_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    monkeypatch.setattr(
        "integrations.openclaw.delivery.openclaw_runtime_unavailable_reason",
        lambda _config: None,
    )

    def _fake_call(_config: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((tool_name, arguments))
        return {"is_error": False, "tool": tool_name, "arguments": arguments, "text": "ok"}

    monkeypatch.setattr("integrations.openclaw.delivery.call_openclaw_tool", _fake_call)

    posted, error = send_openclaw_report(
        _state(),
        "Full RCA report",
        _creds(),
    )

    assert posted is False
    assert error == MISSING_CONVERSATION_ID
    assert calls == []


def test_send_openclaw_report_invalid_config_returns_false() -> None:
    posted, error = send_openclaw_report(
        _state(),
        "report",
        {"mode": "stdio", "command": "", "args": [], "url": ""},
    )

    assert posted is False
    assert error is not None
    assert "invalid" in error.lower()


def test_send_openclaw_report_runtime_unavailable_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.openclaw.delivery.openclaw_runtime_unavailable_reason",
        lambda _config: "Command not found: openclaw",
    )

    posted, error = send_openclaw_report(
        _state(channel_contexts={"openclaw": {"conversation_id": "conv-1"}}),
        "report",
        _creds(mode="stdio", command="openclaw"),
    )

    assert posted is False
    assert error == "Command not found: openclaw"


def test_send_openclaw_report_tool_error_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.openclaw.delivery.openclaw_runtime_unavailable_reason",
        lambda _config: None,
    )
    monkeypatch.setattr(
        "integrations.openclaw.delivery.call_openclaw_tool",
        lambda _config, _tool_name, _arguments: {
            "is_error": True,
            "text": "route missing",
        },
    )

    posted, error = send_openclaw_report(
        _state(channel_contexts={"openclaw": {"conversation_id": "conv-1"}}),
        "report",
        _creds(),
    )

    assert posted is False
    assert error == "route missing"


def test_send_openclaw_report_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.openclaw.delivery.openclaw_runtime_unavailable_reason",
        lambda _config: None,
    )
    monkeypatch.setattr(
        "integrations.openclaw.delivery.call_openclaw_tool",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    posted, error = send_openclaw_report(
        _state(channel_contexts={"openclaw": {"conversation_id": "conv-1"}}),
        "report",
        _creds(),
    )

    assert posted is False
    assert error is not None
    assert "boom" in error


def test_send_openclaw_report_uses_creds_conversation_id_when_context_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    monkeypatch.setattr(
        "integrations.openclaw.delivery.openclaw_runtime_unavailable_reason",
        lambda _config: None,
    )

    def _fake_call(_config: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((tool_name, arguments))
        return {"is_error": False, "tool": tool_name, "arguments": arguments}

    monkeypatch.setattr("integrations.openclaw.delivery.call_openclaw_tool", _fake_call)

    posted, error = send_openclaw_report(
        _state(),
        "report",
        _creds(openclaw_conversation_id="conv-from-creds"),
    )

    assert posted is True
    assert error is None
    assert calls[0][1]["session_key"] == "conv-from-creds"


def test_send_openclaw_report_forwards_conversation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    monkeypatch.setattr(
        "integrations.openclaw.delivery.openclaw_runtime_unavailable_reason",
        lambda _config: None,
    )

    def _fake_call(_config: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((tool_name, arguments))
        return {"is_error": False, "tool": tool_name, "arguments": arguments}

    monkeypatch.setattr("integrations.openclaw.delivery.call_openclaw_tool", _fake_call)

    posted, error = send_openclaw_report(
        _state(channel_contexts={"openclaw": {"conversation_id": "conv-1"}}),
        "report",
        _creds(),
    )

    assert posted is True
    assert error is None
    assert calls == [
        (
            "messages_send",
            {
                "session_key": "conv-1",
                "text": (
                    "report\n\nRoot cause: A bad deploy introduced 5xx errors.\n\n"
                    "Remediation steps:\n- Roll back the deploy\n- Verify health checks\n\n"
                    "Confidence: 92%"
                ),
            },
        )
    ]


def test_send_openclaw_report_merges_transport_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_configs: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "integrations.openclaw.delivery.openclaw_runtime_unavailable_reason",
        lambda config: seen_configs.append((config.mode, config.command)) or None,
    )
    monkeypatch.setattr(
        "integrations.openclaw.delivery.call_openclaw_tool",
        lambda _config, tool_name, arguments: {
            "is_error": False,
            "tool": tool_name,
            "arguments": arguments,
        },
    )

    posted, error = send_openclaw_report(
        _state(
            channel_contexts={
                "openclaw": {
                    "conversation_id": "conv-1",
                    "mode": "stdio",
                    "command": "openclaw",
                    "args": ["mcp", "serve"],
                }
            }
        ),
        "report",
        _creds(),
    )

    assert posted is True
    assert error is None
    assert seen_configs[0] == ("stdio", "openclaw")


def test_openclaw_adapter_skips_when_session_is_missing(caplog: pytest.LogCaptureFixture) -> None:
    from integrations.openclaw.reporting_adapter import openclaw_delivery_adapter

    with caplog.at_level(logging.DEBUG, logger="integrations.openclaw.reporting_adapter"):
        delivered = openclaw_delivery_adapter.deliver(
            {
                "resolved_integrations": {
                    "openclaw": {
                        "mode": "streamable-http",
                        "url": "https://openclaw.example.com/mcp",
                        "auth_token": "tok",
                    }
                },
                "channel_contexts": {"openclaw": {}},
            },
            messages={"slack_text": "RCA report"},
            blocks=[],
        )

    assert delivered is False
    assert any("skipped" in rec.getMessage() for rec in caplog.records)
    assert not any("OpenClaw delivery failed" in rec.getMessage() for rec in caplog.records)


def test_openclaw_adapter_returns_true_after_failed_send(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.openclaw.reporting_adapter import openclaw_delivery_adapter

    monkeypatch.setattr(
        "integrations.openclaw.delivery.openclaw_runtime_unavailable_reason",
        lambda _config: None,
    )
    monkeypatch.setattr(
        "integrations.openclaw.delivery.call_openclaw_tool",
        lambda *_args, **_kwargs: {
            "is_error": True,
            "text": "Unknown conversation_id 'conv-1' on this workspace.",
        },
    )

    with caplog.at_level(logging.WARNING, logger="integrations.openclaw.reporting_adapter"):
        delivered = openclaw_delivery_adapter.deliver(
            {
                "resolved_integrations": {
                    "openclaw": {
                        "mode": "streamable-http",
                        "url": "https://openclaw.example.com/mcp",
                        "auth_token": "tok",
                    }
                },
                "channel_contexts": {"openclaw": {"conversation_id": "conv-1"}},
            },
            messages={"slack_text": "RCA report"},
            blocks=[],
        )

    assert delivered is True
    assert any("OpenClaw delivery failed" in rec.getMessage() for rec in caplog.records)
