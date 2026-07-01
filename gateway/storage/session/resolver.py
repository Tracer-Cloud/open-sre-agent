"""Resolve or create persisted ReplSession instances for gateway chats."""

from __future__ import annotations

import logging
from typing import Any

from core.agent_harness.session import DEFAULT_SESSION_REPO, DEFAULT_SESSION_STORAGE, ReplSession
from gateway.session.gateway_chat_context import inject_gateway_chat_context
from gateway.storage.session.bindings import SessionBindingStore
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


def _prepare_session_for_turn(session: ReplSession, *, chat_id: str) -> ReplSession:
    """Warm integrations at bootstrap time; attach per-turn gateway metadata here."""
    session.resolved_integrations_cache = inject_gateway_chat_context(
        dict(session.resolved_integrations_cache or {}),
        chat_id,
    )
    return session


def _restore_session_context(session: ReplSession, data: dict[str, Any] | None) -> ReplSession:
    if not data:
        return session
    messages = data.get("cli_agent_messages")
    if isinstance(messages, list):
        restored: list[tuple[str, str]] = []
        for item in messages:
            try:
                role, content = item
            except (TypeError, ValueError):
                continue
            if role in {"user", "assistant"} and isinstance(content, str) and content:
                restored.append((role, content))
        session.cli_agent_messages = restored
    context = data.get("accumulated_context")
    if isinstance(context, dict):
        session.accumulated_context = dict(context)
    history = data.get("history")
    if isinstance(history, list):
        session.history = [dict(item) for item in history if isinstance(item, dict)]
    return session


class SessionResolver:
    """Load/create ReplSession objects backed by JSONL session files."""

    def __init__(self, bindings: SessionBindingStore) -> None:
        self._bindings = bindings
        self._storage = DEFAULT_SESSION_STORAGE
        self._repo = DEFAULT_SESSION_REPO

    def resolve(self, *, user_id: str, chat_id: str) -> ReplSession:
        """Return a hydrated session for the Telegram DM user id."""
        existing = self._bindings.get_session_id(platform=_PLATFORM_TELEGRAM, chat_id=user_id)
        if existing:
            data = self._repo.load_session(existing)
            session = _prepare_session_for_turn(
                _restore_session_context(
                    _bootstrap_session(ReplSession(session_id=existing)), data
                ),
                chat_id=chat_id,
            )
            self._storage.reopen_session(session.session_id)
            return session

        session = _prepare_session_for_turn(_bootstrap_session(ReplSession()), chat_id=chat_id)
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

    def rotate(self, *, user_id: str, chat_id: str) -> ReplSession:
        """Flush the current session file and start a new binding."""
        existing = self._bindings.get_session_id(platform=_PLATFORM_TELEGRAM, chat_id=user_id)
        if existing:
            old = ReplSession(session_id=existing)
            try:
                self._storage.flush(old)
            except OSError:
                logger.debug("[gateway] flush failed during rotate", exc_info=True)

        new_id = self._bindings.rotate(platform=_PLATFORM_TELEGRAM, chat_id=user_id)
        session = _prepare_session_for_turn(
            _bootstrap_session(ReplSession(session_id=new_id)),
            chat_id=chat_id,
        )
        self._storage.open_session(session)
        return session
