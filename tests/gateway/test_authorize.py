from __future__ import annotations

from unittest.mock import patch

import pytest

from gateway.security.authorize import evaluate_inbound, persist_policy_if_needed
from integrations.messaging_security import MessagingIdentityPolicy


@pytest.fixture
def mock_integration_store():
    with (
        patch("gateway.security.authorize.get_integration", return_value=None),
        patch("gateway.security.authorize.upsert_instance") as upsert,
    ):
        yield upsert


def test_help_is_not_agent_turn(mock_integration_store: pytest.MonkeyPatch) -> None:
    decision = evaluate_inbound(
        user_id="42",
        chat_id="42",
        text="/help",
        env_allowed_user_ids=["42"],
    )
    assert decision.allowed is False
    assert "OpenSRE Telegram gateway" in decision.reply_text


def test_unauthorized_user_gets_reason(mock_integration_store: pytest.MonkeyPatch) -> None:
    decision = evaluate_inbound(
        user_id="99",
        chat_id="99",
        text="hello",
        env_allowed_user_ids=["42"],
    )
    assert decision.allowed is False
    assert decision.reply_text


def test_pair_attempt_persists_policy(mock_integration_store: pytest.MonkeyPatch) -> None:
    policy = MessagingIdentityPolicy(
        inbound_enabled=True,
        pairing_secret_hash="abc",
    )
    with (
        patch(
            "gateway.security.authorize._load_policy",
            return_value=(None, policy),
        ),
        patch(
            "gateway.security.authorize.complete_pairing",
            return_value=(True, "Pairing successful!"),
        ),
    ):
        decision = evaluate_inbound(
            user_id="42",
            chat_id="42",
            text="/pair CODE",
            env_allowed_user_ids=[],
        )
    assert decision.persist_policy is True
    persist_policy_if_needed(decision)
    mock_integration_store.assert_called_once()
