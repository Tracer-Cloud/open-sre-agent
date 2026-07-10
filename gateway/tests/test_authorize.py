from __future__ import annotations

from unittest.mock import patch

import pytest

from gateway.session.inbound_message_security import (
    enforce_inbound_telegram_message_security,
    persist_policy_if_needed,
)
from integrations.messaging_security import MessagingIdentityPolicy

_SECURITY = "gateway.session.inbound_message_security"


@pytest.fixture
def mock_integration_store():
    with (
        patch(f"{_SECURITY}.get_integration", return_value=None),
        patch(f"{_SECURITY}.upsert_instance") as upsert,
    ):
        yield upsert


@pytest.mark.usefixtures("mock_integration_store")
def test_help_is_not_agent_turn() -> None:
    decision = enforce_inbound_telegram_message_security(
        user_id="42",
        chat_id="42",
        text="/help",
        env_allowed_user_ids=["42"],
    )
    assert decision.allowed is False
    assert "OpenSRE Telegram gateway" in decision.reply_text


@pytest.mark.usefixtures("mock_integration_store")
def test_unauthorized_user_gets_reason() -> None:
    decision = enforce_inbound_telegram_message_security(
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
            f"{_SECURITY}._load_policy",
            return_value=(None, policy),
        ),
        patch(
            f"{_SECURITY}.complete_pairing",
            return_value=(True, "Pairing successful!"),
        ),
    ):
        decision = enforce_inbound_telegram_message_security(
            user_id="42",
            chat_id="42",
            text="/pair CODE",
            env_allowed_user_ids=[],
        )
    assert decision.persist_policy is True
    persist_policy_if_needed(decision)
    mock_integration_store.assert_called_once()


@pytest.mark.usefixtures("mock_integration_store")
def test_unauthorized_user_cannot_rotate_session() -> None:
    decision = enforce_inbound_telegram_message_security(
        user_id="99",
        chat_id="99",
        text="/new",
        env_allowed_user_ids=["42"],
    )
    assert decision.allowed is False
    assert decision.reply_text
    assert decision.reply_text != "__ROTATE_SESSION__"


@pytest.mark.usefixtures("mock_integration_store")
def test_authorized_user_can_rotate_session() -> None:
    decision = enforce_inbound_telegram_message_security(
        user_id="42",
        chat_id="42",
        text="/new",
        env_allowed_user_ids=["42"],
    )
    assert decision.allowed is True
    assert decision.reply_text == "__ROTATE_SESSION__"
