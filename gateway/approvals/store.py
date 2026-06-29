"""Pending approval persistence for Telegram inline keyboards."""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import cast


class ApprovalStore:
    """Track pending tool-approval requests."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(
        self,
        *,
        chat_id: str,
        message_id: str,
        tool_name: str,
        payload_hash: str,
        expires_at: float,
    ) -> str:
        approval_id = uuid.uuid4().hex[:12]
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO gateway_approval_requests
            (approval_id, chat_id, message_id, tool_name, payload_hash, status, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (approval_id, chat_id, message_id, tool_name, payload_hash, expires_at, now),
        )
        self._conn.commit()
        return approval_id

    def resolve(self, approval_id: str, *, status: str) -> sqlite3.Row | None:
        row = self._conn.execute(
            "SELECT * FROM gateway_approval_requests WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if row is None:
            return None
        self._conn.execute(
            "UPDATE gateway_approval_requests SET status = ? WHERE approval_id = ?",
            (status, approval_id),
        )
        self._conn.commit()
        return cast(sqlite3.Row | None, row)

    def get(self, approval_id: str) -> sqlite3.Row | None:
        row = self._conn.execute(
            "SELECT * FROM gateway_approval_requests WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        return cast(sqlite3.Row | None, row)
