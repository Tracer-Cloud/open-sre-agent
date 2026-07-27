"""Session binding store construction."""

from __future__ import annotations

from typing import Protocol

from config.principal import Actor, Principal
from gateway.storage.db import BindingsConnections
from gateway.storage.session.bindings import SessionBindingStore


class BindingStore(Protocol):
    """Contract the session resolver depends on, independent of the backend."""

    def get_session_id(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> str | None:
        raise NotImplementedError

    def bind(
        self,
        *,
        platform: str,
        chat_id: str,
        session_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> None:
        raise NotImplementedError

    def rotate(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> str:
        raise NotImplementedError

    def has_any_actor_binding(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
    ) -> bool:
        raise NotImplementedError


def open_binding_store() -> SessionBindingStore:
    """Return the binding store, resolving its database per bound organization."""
    return SessionBindingStore(BindingsConnections())


__all__ = ["BindingStore", "open_binding_store"]
