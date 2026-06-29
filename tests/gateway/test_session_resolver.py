from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gateway.session.resolver import SessionResolver


@pytest.mark.xfail(
    strict=True,
    reason="bug: resolve() reopens the session but never reloads the persisted "
    "transcript into agent.messages, so every inbound message starts amnesiac",
)
def test_resolve_rehydrates_persisted_transcript() -> None:
    bindings = MagicMock()
    bindings.get_session_id.return_value = "sess-1"
    resolver = SessionResolver(bindings)

    fake_storage = MagicMock()
    fake_storage.load_session.return_value = {
        "session_id": "sess-1",
        "cli_agent_messages": [("user", "hi"), ("assistant", "hello")],
        "accumulated_context": {},
        "history": [],
    }
    resolver._storage = fake_storage

    # Identity bootstrap so the test does not warm real integrations / hit the network.
    with patch("gateway.session.resolver._bootstrap_session", side_effect=lambda s: s):
        session = resolver.resolve(user_id="42")

    assert session.agent.messages == [("user", "hi"), ("assistant", "hello")]
