"""SQLite-backed mapping from platform chat ids to ReplSession ids."""

from __future__ import annotations

import sqlite3
import time
import uuid


class SessionBindingStore:
    """Persist external chat -> OpenSRE session id bindings."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_session_id(self, *, platform: str, chat_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT session_id FROM gateway_session_bindings WHERE platform = ? AND chat_id = ?",
            (platform, chat_id),
        ).fetchone()
        if row is None:
            return None
        return str(row["session_id"])

    def bind(self, *, platform: str, chat_id: str, session_id: str) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO gateway_session_bindings (platform, chat_id, session_id, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(platform, chat_id) DO UPDATE SET
                session_id = excluded.session_id,
                updated_at = excluded.updated_at
            """,
            (platform, chat_id, session_id, now),
        )
        self._conn.commit()

    def rotate(self, *, platform: str, chat_id: str) -> str:
        """Assign a fresh session id for the chat binding."""
        new_id = str(uuid.uuid4())
        self.bind(platform=platform, chat_id=chat_id, session_id=new_id)
        return new_id
