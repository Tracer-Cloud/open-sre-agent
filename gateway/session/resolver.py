"""Resolve or create persisted ReplSession instances for gateway chats."""

from __future__ import annotations

import logging

from core.agent_harness.session import DEFAULT_SESSION_STORAGE, ReplSession
from gateway.session.bindings import SessionBindingStore
from surfaces.interactive_shell.runtime.context import ReplSessionBootstrapSpec

logger = logging.getLogger(__name__)

_PLATFORM_TELEGRAM = "telegram"


def _bootstrap_session(session: ReplSession) -> ReplSession:
    spec = ReplSessionBootstrapSpec(
        session=session,
        hydrate_integrations=True,
        persistent_tasks=True,
    )
    spec.session.warm_resolved_integrations()
    return spec.session


class SessionResolver:
    """Load/create ReplSession objects backed by JSONL session files."""

    def __init__(self, bindings: SessionBindingStore) -> None:
        self._bindings = bindings
        self._storage = DEFAULT_SESSION_STORAGE

    def resolve(self, *, user_id: str) -> ReplSession:
        """Return a hydrated session for the Telegram DM user id."""
        existing = self._bindings.get_session_id(platform=_PLATFORM_TELEGRAM, chat_id=user_id)
        if existing:
            session = _bootstrap_session(ReplSession(session_id=existing))
            self._storage.reopen_session(session.session_id)
            return session

        session = _bootstrap_session(ReplSession())
        self._storage.open_session(session)
        self._bindings.bind(
            platform=_PLATFORM_TELEGRAM,
            chat_id=user_id,
            session_id=session.session_id,
        )
        logger.info(
            "[gateway] created session %s for telegram user %s",
            session.session_id,
            user_id,
        )
        return session

    def rotate(self, *, user_id: str) -> ReplSession:
        """Flush the current session file and start a new binding."""
        existing = self._bindings.get_session_id(platform=_PLATFORM_TELEGRAM, chat_id=user_id)
        if existing:
            old = ReplSession(session_id=existing)
            try:
                self._storage.flush(old)
            except OSError:
                logger.debug("[gateway] flush failed during rotate", exc_info=True)

        new_id = self._bindings.rotate(platform=_PLATFORM_TELEGRAM, chat_id=user_id)
        session = _bootstrap_session(ReplSession(session_id=new_id))
        self._storage.open_session(session)
        return session
