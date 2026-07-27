"""Session binding store construction."""

from __future__ import annotations

from typing import Protocol

from config.principal import Actor, Principal
from gateway.storage.db import connect_gateway_db
from gateway.storage.session.bindings import SessionBindingStore


class BindingStore(Protocol):
    def get_session_id(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> str | None: ...

    def bind(
        self,
        *,
        platform: str,
        chat_id: str,
        session_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> None: ...

    def rotate(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> str: ...

    def has_any_actor_binding(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
    ) -> bool: ...


def open_binding_store() -> SessionBindingStore:
    """Return the SQLite-backed binding store for this gateway."""
    return SessionBindingStore(connect_gateway_db())


__all__ = ["BindingStore", "open_binding_store"]
