"""Resolve or create persisted Session instances for gateway chats.

Session lifecycle (create / resolve / rotate / restore / flush) is owned by
:class:`core.agent_harness.session.SessionManager`. This resolver adds only the
gateway-specific concerns on top: the platform chat-id ↔ session-id binding
store (principal-scoped) and per-turn gateway chat metadata.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.agent_harness.session import SessionCore, SessionManager
from core.agent_harness.session.integration_resolution import has_resolved_integrations
from gateway.session.gateway_chat_context import inject_gateway_chat_context

if TYPE_CHECKING:
    from config.principal import Principal
    from gateway.storage.session.binding_store import BindingStore

logger = logging.getLogger(__name__)

_PLATFORM_TELEGRAM = "telegram"


def _ensure_integrations(session: SessionCore) -> SessionCore:
    """Re-hydrate configured names and warm configs when the cache is empty.

    Gateway turns must see Slack (and other store/env integrations) even when a
    prior session left a metadata-only or empty resolved cache.
    """
    session.hydrate_configured_integrations()
    if not has_resolved_integrations(session.resolved_integrations_cache):
        session.warm_resolved_integrations()
    return session


def _inject_chat_context(session: SessionCore, *, chat_id: str, platform: str = "") -> SessionCore:
    """Attach per-turn gateway chat metadata to the session's integration cache."""
    _ensure_integrations(session)
    session.resolved_integrations_cache = inject_gateway_chat_context(
        dict(session.resolved_integrations_cache or {}),
        chat_id,
        platform,
    )
    return session


class SessionResolver:
    """Bind platform conversations to sessions, delegating lifecycle to SessionManager."""

    def __init__(
        self,
        bindings: BindingStore,
        *,
        manager: SessionManager | None = None,
        platform: str = _PLATFORM_TELEGRAM,
    ) -> None:
        self._bindings = bindings
        self._manager = manager or SessionManager()
        self._platform = platform

    def resolve(
        self,
        *,
        user_id: str,
        chat_id: str,
        principal: Principal | None = None,
    ) -> SessionCore:
        """Return a hydrated session for the platform conversation key ``user_id``.

        Slack team turns pass an org ``principal``. Other surfaces omit it and
        keep main-branch binding behavior (legacy empty principal id).
        """
        existing = self._bindings.get_session_id(
            platform=self._platform,
            chat_id=user_id,
            principal=principal,
        )
        if existing:
            session = self._manager.resolve(existing)
            return _inject_chat_context(session, chat_id=chat_id, platform=self._platform)

        session = self._manager.create(warm_integrations=True)
        _inject_chat_context(session, chat_id=chat_id, platform=self._platform)
        self._bindings.bind(
            platform=self._platform,
            chat_id=user_id,
            session_id=session.session_id,
            principal=principal,
        )
        logger.info(
            "[gateway] created session %s for %s conversation %s principal=%s",
            session.session_id,
            self._platform,
            user_id,
            principal.id if principal is not None else "",
        )
        return session

    def has_session(self, *, user_id: str, principal: Principal | None = None) -> bool:
        """Whether the bot already has a session bound to this conversation key."""
        return bool(
            self._bindings.get_session_id(
                platform=self._platform,
                chat_id=user_id,
                principal=principal,
            )
        )

    def rotate(
        self,
        *,
        user_id: str,
        chat_id: str,
        principal: Principal | None = None,
    ) -> SessionCore:
        """Flush the current session file and start a new binding."""
        existing = self._bindings.get_session_id(
            platform=self._platform,
            chat_id=user_id,
            principal=principal,
        )
        new_id = self._bindings.rotate(
            platform=self._platform,
            chat_id=user_id,
            principal=principal,
        )
        session = self._manager.rotate(old_session_id=existing or None, new_session_id=new_id)
        return _inject_chat_context(session, chat_id=chat_id, platform=self._platform)
