from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_harness.models.turn_snapshot import TurnSnapshot
from core.agent_harness.prompts import build_action_system_prompt
from core.agent_harness.session import InMemorySessionStorage, Session, SessionManager
from gateway.storage import SessionBindingStore, SessionResolver, connect_gateway_db


@pytest.fixture
def resolver(tmp_path, monkeypatch) -> SessionResolver:
    # Keep integration bootstrap a no-op so tests don't resolve real integrations.
    monkeypatch.setattr(Session, "warm_resolved_integrations", lambda _self, **_k: None)
    monkeypatch.setattr(Session, "hydrate_configured_integrations", lambda _self: None)

    conn = connect_gateway_db(tmp_path / "state.db")
    store = SessionBindingStore(conn)
    # A mutable fake repo whose load_session each test can override.
    repo = SimpleNamespace(load_session=lambda _session_id: None)
    manager = SessionManager(storage=InMemorySessionStorage(), repo=repo)
    resolver = SessionResolver(store, manager=manager)
    resolver._fake_repo = repo  # test handle to swap load_session
    yield resolver
    conn.close()


def test_resolve_creates_and_injects_gateway_chat_context(resolver: SessionResolver) -> None:
    resolved = resolver.resolve(user_id="42", chat_id="99")

    # New session was created, bound, and tagged with the per-turn chat id.
    assert resolved.resolved_integrations_cache["_gateway_chat_id"] == "99"
    assert (
        resolver._bindings.get_session_id(platform="telegram", chat_id="42") == resolved.session_id
    )


def test_resolve_restores_persisted_conversation_context(resolver: SessionResolver) -> None:
    resolver._bindings.bind(platform="telegram", chat_id="42", session_id="session-1")
    resolver._fake_repo.load_session = lambda session_id: {
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
    resolver: SessionResolver,
) -> None:
    resolver._bindings.bind(platform="telegram", chat_id="42", session_id="session-1")
    resolver._fake_repo.load_session = lambda session_id: {
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

    resolved = resolver.resolve(user_id="42", chat_id="99")

    prompt = build_action_system_prompt(
        TurnSnapshot.from_session(
            "No, compute those temperatures and send the nice comparison to Slack",
            resolved,
        )
    )

    assert "PRIOR ACTION FACTS" in prompt
    assert "Hawaii: +28C" in prompt
    assert "Antarctica: -24C" in prompt
    assert "London: +22C" in prompt
    assert "slack_send_message input" in prompt


def test_rotate_flushes_old_and_binds_new(resolver: SessionResolver) -> None:
    first = resolver.resolve(user_id="42", chat_id="99")
    rotated = resolver.rotate(user_id="42", chat_id="99")

    assert rotated.session_id != first.session_id
    assert rotated.resolved_integrations_cache["_gateway_chat_id"] == "99"
    assert (
        resolver._bindings.get_session_id(platform="telegram", chat_id="42") == rotated.session_id
    )
