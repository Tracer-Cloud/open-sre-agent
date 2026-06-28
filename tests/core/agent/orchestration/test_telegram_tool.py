"""Tests for the Telegram interactive-shell action tool."""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

import tools.interactive_shell.actions.telegram_message as telegram_action
from core.agent_harness.action_agent import _EXECUTED_HISTORY_TYPES
from core.agent_harness.session import InMemorySessionStorage, ReplSession
from tools.interactive_shell.contracts import ToolContext


def _ctx() -> tuple[ToolContext, io.StringIO, ReplSession]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False)
    session = ReplSession(storage=InMemorySessionStorage())
    return ToolContext(session=session, console=console), buf, session


def test_telegram_action_calls_registered_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"sent": True, "chat_id": kwargs["chat_id"], "error": ""}

    monkeypatch.setattr(telegram_action, "_run_telegram_send_message", _fake_run)

    ctx, buf, session = _ctx()
    handled = telegram_action.execute_telegram_send_message_tool(
        {
            "message": " page on-call ",
            "chat_id": " -100123 ",
            "reply_to_message_id": " 42 ",
        },
        ctx,
    )

    assert handled is True
    assert captured == {
        "message": "page on-call",
        "chat_id": "-100123",
        "reply_to_message_id": "42",
    }
    assert "telegram message sent" in buf.getvalue()
    assert session.history[-1] == {
        "type": "telegram_send_message",
        "text": "page on-call",
        "ok": True,
    }


def test_telegram_action_reports_delivery_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(**_kwargs: Any) -> dict[str, Any]:
        return {"sent": False, "error": "missing default chat"}

    monkeypatch.setattr(telegram_action, "_run_telegram_send_message", _fake_run)

    ctx, buf, session = _ctx()
    handled = telegram_action.execute_telegram_send_message_tool(
        {"message": "notify responders"},
        ctx,
    )

    assert handled is True
    assert "missing default chat" in buf.getvalue()
    assert session.history[-1]["ok"] is False


def test_telegram_history_counts_as_executed_action() -> None:
    assert "telegram_send_message" in _EXECUTED_HISTORY_TYPES
