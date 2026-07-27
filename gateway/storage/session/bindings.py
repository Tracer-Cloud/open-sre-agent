"""Mapping from (platform, chat, principal) to Session ids."""

from __future__ import annotations

import sqlite3
import time
import uuid

from config.principal import Principal

# Non-Slack surfaces (Telegram, etc.) omit principal and share this id so
# lookups match pre-principal rows migrated with an empty principal_id.
_LEGACY_PRINCIPAL_ID = ""


def _principal_id(principal: Principal | None) -> str:
    return _LEGACY_PRINCIPAL_ID if principal is None else principal.id


class SessionBindingStore:
    """Persist external chat -> OpenSRE session id bindings.

    Slack team turns pass an org :class:`Principal`. Other surfaces may omit
    it; bindings then use the legacy empty ``principal_id`` (same as main).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def close(self) -> None:
        """Release the underlying connection."""
        self._conn.close()

    def get_session_id(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
    ) -> str | None:
        row = self._conn.execute(
            """
            SELECT session_id FROM gateway_session_bindings
            WHERE platform = ? AND chat_id = ? AND principal_id = ?
            """,
            (platform, chat_id, _principal_id(principal)),
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
    ) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO gateway_session_bindings
                (platform, chat_id, principal_id, session_id, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(platform, chat_id, principal_id) DO UPDATE SET
                session_id = excluded.session_id,
                updated_at = excluded.updated_at
            """,
            (platform, chat_id, _principal_id(principal), session_id, now),
        )
        self._conn.commit()

    def rotate(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
    ) -> str:
        """Assign a fresh session id for the chat binding under ``principal``."""
        new_id = str(uuid.uuid4())
        self.bind(
            platform=platform,
            chat_id=chat_id,
            session_id=new_id,
            principal=principal,
        )
        return new_id
