from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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
