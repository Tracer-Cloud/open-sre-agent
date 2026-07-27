"""Mapping from (platform, chat, principal, actor) to Session ids."""

from __future__ import annotations

import sqlite3
import time
import uuid
from collections.abc import Callable

from config.principal import Actor, Principal

# Resolves the database for the organization bound to the current turn.
ConnectionSource = Callable[[], sqlite3.Connection]

# Non-Slack surfaces omit principal/actor and share these ids so lookups match
# pre-scoped rows migrated with empty principal_id / actor_id.
_LEGACY_PRINCIPAL_ID = ""
_LEGACY_ACTOR_ID = ""


def _principal_id(principal: Principal | None) -> str:
    return _LEGACY_PRINCIPAL_ID if principal is None else principal.id


def _actor_id(actor: Actor | str | None) -> str:
    if actor is None:
        return _LEGACY_ACTOR_ID
    if isinstance(actor, str):
        return actor.strip()
    return actor.id


class SessionBindingStore:
    """Persist external chat -> OpenSRE session id bindings.

    Slack team turns pass an org :class:`Principal` and Slack :class:`Actor`.
    Other surfaces may omit both; bindings then use legacy empty ids.
    """

    def __init__(self, conn: sqlite3.Connection | ConnectionSource) -> None:
        # sqlite3.Connection is itself callable, so test the concrete type.
        if isinstance(conn, sqlite3.Connection):
            fixed: sqlite3.Connection = conn
            self._owned: sqlite3.Connection | None = fixed
            self._source: ConnectionSource = lambda: fixed
        else:
            self._owned = None
            self._source = conn

    @property
    def _conn(self) -> sqlite3.Connection:
        """The database for the organization bound right now."""
        return self._source()

    def close(self) -> None:
        """Release connections this store opened."""
        if self._owned is not None:
            self._owned.close()
            return
        closer = getattr(self._source, "close", None)
        if callable(closer):
            closer()

    def has_any_actor_binding(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
    ) -> bool:
        """Whether any member has a session bound to this conversation.

        Sessions are per member, so "has the bot joined this thread" is a
        question about the conversation rather than about one member.
        """
        row = self._conn.execute(
            """
            SELECT 1 FROM gateway_session_bindings
            WHERE platform = ? AND chat_id = ? AND principal_id = ?
            LIMIT 1
            """,
            (platform, chat_id, _principal_id(principal)),
        ).fetchone()
        return row is not None

    def get_session_id(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> str | None:
        row = self._conn.execute(
            """
            SELECT session_id FROM gateway_session_bindings
            WHERE platform = ? AND chat_id = ? AND principal_id = ? AND actor_id = ?
            """,
            (platform, chat_id, _principal_id(principal), _actor_id(actor)),
        ).fetchone()
        if row is None:
            return None
        return str(row["session_id"])

    def bind(
        self,
        *,
        platform: str,
        chat_id: str,
        session_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO gateway_session_bindings
                (platform, chat_id, principal_id, actor_id, session_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, chat_id, principal_id, actor_id) DO UPDATE SET
                session_id = excluded.session_id,
                updated_at = excluded.updated_at
            """,
            (
                platform,
                chat_id,
                _principal_id(principal),
                _actor_id(actor),
                session_id,
                now,
            ),
        )
        self._conn.commit()

    def rotate(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> str:
        """Assign a fresh session id for the chat binding under principal+actor."""
        new_id = str(uuid.uuid4())
        self.bind(
            platform=platform,
            chat_id=chat_id,
            session_id=new_id,
            principal=principal,
            actor=actor,
        )
        return new_id
