"""A stored schedule must have somewhere to deliver.

Slack may omit ``--chat-id`` only when an incoming webhook is configured — the
webhook is bound to one channel, so the scheduler has a destination. On a
bot-token-only install there is no webhook and no channel, so the task is
accepted and then delivers nowhere. Sixteen such rows accumulated during manual
testing before anyone noticed.
"""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from surfaces.cli.commands.cron import cron_add


def _add(monkeypatch: pytest.MonkeyPatch, *, webhook: Any) -> Any:
    def _resolve(_url: str = "") -> tuple[Any, str]:
        return (webhook, "" if webhook else "Slack is not configured.")

    monkeypatch.setattr(
        "integrations.slack.tools.slack_send_message_tool.delivery.resolve_webhook_url",
        _resolve,
    )
    return CliRunner().invoke(
        cron_add,
        ["--kind", "daily_summary", "--cron", "0 8 * * 1-5", "--provider", "slack"],
    )


def test_slack_without_a_webhook_or_chat_id_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bot-token-only installs must not store a task with no destination."""
    # Act
    result = _add(monkeypatch, webhook=None)

    # Assert
    assert result.exit_code != 0, result.output
    assert "chat-id" in result.output.lower() or "webhook" in result.output.lower()
