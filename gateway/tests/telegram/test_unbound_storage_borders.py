"""Telegram gateway stays unbound — Slack org scoping must not touch it."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import paths
from config.principal import Actor, Principal
from config.scope_context import current_scope
from gateway.core.storage import FileBindingStore
from gateway.transports.telegram.inbound_security import InboundDecision
from gateway.transports.telegram.session_rotation import resolve_or_rotate_session


@pytest.fixture(autouse=True)
def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
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


def test_telegram_resolve_never_passes_principal_or_actor() -> None:
    calls: list[dict[str, object]] = []

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
    )
    assert session is not None
    assert calls == [{"user_id": "tg-7", "chat_id": "chat-7", "kwargs": {}}]


def test_telegram_legacy_binding_unaffected_by_slack_org_rows(tmp_path: Path) -> None:
    store = FileBindingStore(tmp_path / "bindings.json")
    org = Principal.org("org_acme")
    store.bind(
        platform="slack",
        chat_id="T:C:1",
        session_id="slack-sess",
        principal=org,
        actor=Actor(id="U_ALICE"),
    )
    store.bind(platform="telegram", chat_id="chat-7", session_id="tg-sess")

    assert store.get_session_id(platform="telegram", chat_id="chat-7") == "tg-sess"
    assert (
        store.get_session_id(
            platform="telegram",
            chat_id="chat-7",
            principal=org,
            actor="U_ALICE",
        )
        is None
    )
    assert store.get_session_id(platform="slack", chat_id="T:C:1") is None
    store.close()


def test_telegram_paths_stay_flat_when_slack_mount_is_configured(
    _host: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same ECS task may have OPENSRE_CONTEXT_ROOT; Telegram must ignore it."""
    mount = _host / "workspace" / "memories"
    mount.mkdir(parents=True)
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(mount))

    assert current_scope() is None
    assert paths.opensre_home() == _host
    assert paths.session_home() == _host
    assert paths.integrations_store_path() == _host / "integrations.json"
    assert mount not in paths.session_home().parents
