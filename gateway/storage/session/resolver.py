"""Resolve or create persisted Session instances for gateway chats.

Session lifecycle (create / resolve / rotate / restore / flush) is owned by
:class:`core.agent_harness.session.SessionManager`. This resolver adds only the
gateway-specific concerns on top: the platform chat-id ↔ session-id binding
store and per-turn gateway chat metadata. It does not re-implement bootstrap or
persistence, and it does not depend on any other surface.
"""

from __future__ import annotations

import logging

from core.agent_harness.session import SessionCore, SessionManager
from gateway.session.gateway_chat_context import inject_gateway_chat_context
from gateway.storage.session.bindings import SessionBindingStore

logger = logging.getLogger(__name__)

_PLATFORM_TELEGRAM = "telegram"


def _inject_chat_context(session: SessionCore, *, chat_id: str) -> SessionCore:
    """Attach per-turn gateway chat metadata to the session's integration cache."""
    session.resolved_integrations_cache = inject_gateway_chat_context(
        dict(session.resolved_integrations_cache or {}),
        chat_id,
    )
    return session


class SessionResolver:
    """Bind platform conversations to sessions, delegating lifecycle to SessionManager."""

    def __init__(
        self,
        bindings: SessionBindingStore,
        *,
        manager: SessionManager | None = None,
        platform: str = _PLATFORM_TELEGRAM,
    ) -> None:
        self._bindings = bindings
        self._manager = manager or SessionManager()
        self._platform = platform

    def resolve(self, *, user_id: str, chat_id: str) -> SessionCore:
        """Return a hydrated session for the platform conversation key ``user_id``."""
        existing = self._bindings.get_session_id(platform=self._platform, chat_id=user_id)
        if existing:
            session = self._manager.resolve(existing)
            return _inject_chat_context(session, chat_id=chat_id)

        session = self._manager.create(warm_integrations=True)
        _inject_chat_context(session, chat_id=chat_id)
        self._bindings.bind(
            platform=self._platform,
            chat_id=user_id,
            session_id=session.session_id,
        )
        logger.info(
            "[gateway] created session %s for %s conversation %s",
            session.session_id,
            self._platform,
            user_id,
        )
        return session

    def rotate(self, *, user_id: str, chat_id: str) -> SessionCore:
        """Flush the current session file and start a new binding."""
        existing = self._bindings.get_session_id(platform=self._platform, chat_id=user_id)
        new_id = self._bindings.rotate(platform=self._platform, chat_id=user_id)
        session = self._manager.rotate(old_session_id=existing or None, new_session_id=new_id)
        return _inject_chat_context(session, chat_id=chat_id)
