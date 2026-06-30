from __future__ import annotations

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
