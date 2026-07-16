"""The gateway injects the inactive chat transports to hide (transport knowledge)."""

from __future__ import annotations

from gateway.session.gateway_chat_context import inject_gateway_chat_context


def test_slack_turn_hides_other_transports_not_slack() -> None:
    merged = inject_gateway_chat_context({}, chat_id="C1", platform="slack")
    hidden = merged["_gateway_hidden_integrations"]
    assert "telegram" in hidden
    assert "slack" not in hidden


def test_telegram_turn_hides_slack() -> None:
    merged = inject_gateway_chat_context({}, chat_id="123", platform="telegram")
    hidden = merged["_gateway_hidden_integrations"]
    assert "slack" in hidden
    assert "telegram" not in hidden


def test_no_platform_injects_no_hidden_set() -> None:
    merged = inject_gateway_chat_context({}, chat_id="C1")
    assert "_gateway_hidden_integrations" not in merged
