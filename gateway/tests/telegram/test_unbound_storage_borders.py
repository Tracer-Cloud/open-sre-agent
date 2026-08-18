"""Telegram binds org scope + actor; must not import Slack principal modules."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV
from config.principal import Actor, Principal
from config.scope_context import bound_storage_scope, current_scope
from gateway.core.storage import FileBindingStore
from gateway.transports.telegram.inbound_security import InboundDecision
from gateway.transports.telegram.principal import resolve_telegram_scope
from gateway.transports.telegram.session_rotation import resolve_or_rotate_session


@pytest.fixture(autouse=True)
def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_tg")
    return tmp_path


class _Event:
    user_id = "tg-7"
    chat_id = "chat-7"
    text = "status please"


class _Client:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def test_telegram_resolve_passes_principal_and_actor() -> None:
    calls: list[dict[str, object]] = []
    scope = resolve_telegram_scope(user_id="tg-7")

    class _Resolver:
        def resolve(self, *, user_id: str, chat_id: str, **kwargs: object) -> object:
            calls.append({"user_id": user_id, "chat_id": chat_id, "kwargs": kwargs})
            return object()

        def rotate(self, *, user_id: str, chat_id: str, **kwargs: object) -> object:
            calls.append({"rotate": True, "user_id": user_id, "chat_id": chat_id, "kwargs": kwargs})
            return object()

    session = resolve_or_rotate_session(
        _Event(),  # type: ignore[arg-type]
        InboundDecision(allowed=True),
        session_resolver=_Resolver(),  # type: ignore[arg-type]
        client=_Client(),  # type: ignore[arg-type]
        scope=scope,
    )
    assert session is not None
    assert calls == [
        {
            "user_id": "tg-7",
            "chat_id": "chat-7",
            "kwargs": {"principal": scope.principal, "actor": scope.actor},
        }
    ]


def test_telegram_scoped_binding_isolated_from_slack_org_rows(tmp_path: Path) -> None:
    store = FileBindingStore(tmp_path / "bindings.json")
    org = Principal.org("org_acme")
    store.bind(
        platform="slack",
        chat_id="T:C:1",
        session_id="slack-sess",
        principal=org,
        actor=Actor(id="U_ALICE"),
    )
    store.bind(
        platform="telegram",
        chat_id="chat-7",
        session_id="tg-sess",
        principal=org,
        actor=Actor(id="tg-7"),
    )

    assert (
        store.get_session_id(
            platform="telegram",
            chat_id="chat-7",
            principal=org,
            actor="tg-7",
        )
        == "tg-sess"
    )
    assert store.get_session_id(platform="telegram", chat_id="chat-7") is None
    assert (
        store.get_session_id(
            platform="slack",
            chat_id="T:C:1",
            principal=org,
            actor="U_ALICE",
        )
        == "slack-sess"
    )
    store.close()


def test_telegram_bound_scope_uses_org_nest(_host: Path) -> None:
    """With ORGANIZATION_ID and no mount, Telegram turns nest under orgs/<id>/."""
    scope = resolve_telegram_scope(user_id="tg-7")
    with bound_storage_scope(scope):
        assert current_scope() is scope
        home = paths.opensre_home()
        assert home == _host / "orgs" / "org_tg"
        assert paths.integrations_store_path() == home / "integrations.json"
