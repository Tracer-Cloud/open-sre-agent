from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.agent_harness.models.turn_context import TurnContext
from core.agent_harness.prompts import build_action_system_prompt
from gateway.storage import SessionBindingStore, SessionResolver, connect_gateway_db


@pytest.fixture
def resolver(tmp_path) -> SessionResolver:
    conn = connect_gateway_db(tmp_path / "state.db")
    store = SessionBindingStore(conn)
    resolver = SessionResolver(store)
    yield resolver
    conn.close()


@patch("gateway.storage.session.resolver.ReplSessionBootstrapSpec")
def test_resolve_warms_and_injects_gateway_chat_context(
    mock_bootstrap_spec: MagicMock,
    resolver: SessionResolver,
) -> None:
    session = MagicMock()
    session.session_id = "session-1"
    session.resolved_integrations_cache = {"github": {"token": "x"}}

    def _warm() -> None:
        session.resolved_integrations_cache = {"github": {"token": "x"}}

    session.warm_resolved_integrations.side_effect = _warm
    mock_bootstrap_spec.return_value.session = session

    with (
        patch.object(resolver._storage, "open_session"),
        patch.object(resolver._storage, "reopen_session"),
    ):
        resolved = resolver.resolve(user_id="42", chat_id="99")

    session.warm_resolved_integrations.assert_called_once()
    assert resolved.resolved_integrations_cache["github"] == {"token": "x"}
    assert resolved.resolved_integrations_cache["_gateway_chat_id"] == "99"


def test_resolve_restores_persisted_conversation_context(
    monkeypatch: pytest.MonkeyPatch,
    resolver: SessionResolver,
) -> None:
    resolver._bindings.bind(platform="telegram", chat_id="42", session_id="session-1")
    resolver._repo = SimpleNamespace(
        load_session=lambda session_id: {
            "session_id": session_id,
            "cli_agent_messages": [
                ("user", "weather in Hawaii"),
                ("assistant", "Hawaii: +28C"),
                ("user", "send that to Slack"),
                (
                    "assistant",
                    'slack_send_message input: {"message": "Hawaii: +28C"}\n'
                    'slack_send_message result: {"status": "sent"}',
                ),
            ],
            "accumulated_context": {"service": "checkout"},
            "history": [{"type": "shell", "text": "curl wttr.in/Hawaii", "ok": True}],
        }
    )
    monkeypatch.setattr(
        "gateway.storage.session.resolver._bootstrap_session", lambda session: session
    )

    with patch.object(resolver._storage, "reopen_session"):
        resolved = resolver.resolve(user_id="42", chat_id="99")

    assert resolved.cli_agent_messages[-1] == (
        "assistant",
        'slack_send_message input: {"message": "Hawaii: +28C"}\n'
        'slack_send_message result: {"status": "sent"}',
    )
    assert resolved.accumulated_context == {"service": "checkout"}
    assert resolved.history == [{"type": "shell", "text": "curl wttr.in/Hawaii", "ok": True}]
    assert resolved.resolved_integrations_cache["_gateway_chat_id"] == "99"


def test_resolved_telegram_context_is_visible_as_prior_action_facts(
    monkeypatch: pytest.MonkeyPatch,
    resolver: SessionResolver,
) -> None:
    resolver._bindings.bind(platform="telegram", chat_id="42", session_id="session-1")
    resolver._repo = SimpleNamespace(
        load_session=lambda session_id: {
            "session_id": session_id,
            "cli_agent_messages": [
                ("user", "Can you send the weather of both hawaii and antartica to slack?"),
                (
                    "assistant",
                    "Hawaii: +28C\n"
                    "Antarctica: -24C\n"
                    'slack_send_message input: {"message": "Hawaii: +28C\\nAntarctica: -24C"}\n'
                    'slack_send_message result: {"sent": true}',
                ),
                ("user", "Write it in a nicer message and compare to London"),
                ("assistant", "London: +22C"),
            ],
        }
    )
    monkeypatch.setattr(
        "gateway.storage.session.resolver._bootstrap_session", lambda session: session
    )

    with patch.object(resolver._storage, "reopen_session"):
        resolved = resolver.resolve(user_id="42", chat_id="99")

    prompt = build_action_system_prompt(
        TurnContext.from_session(
            "No, compute those temperatures and send the nice comparison to Slack",
            resolved,
        )
    )

    assert "PRIOR ACTION FACTS" in prompt
    assert "Hawaii: +28C" in prompt
    assert "Antarctica: -24C" in prompt
    assert "London: +22C" in prompt
    assert "slack_send_message input" in prompt
