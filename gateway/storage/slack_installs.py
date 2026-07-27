"""Slack team install → Clerk org lookup (principal resolution input)."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

from gateway.storage.db import connect_gateway_db

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlackInstall:
    team_id: str
    clerk_org_id: str
    bot_token: str = ""
    bot_user_id: str = ""


def upsert_slack_install(
    *,
    team_id: str,
    clerk_org_id: str,
    bot_token: str = "",
    bot_user_id: str = "",
    conn: sqlite3.Connection | None = None,
) -> None:
    """Persist or update a Slack team install (SQLite local catalog)."""
    team = team_id.strip()
    org = clerk_org_id.strip()
    if not team or not org:
        raise ValueError("team_id and clerk_org_id are required")
    owns_conn = conn is None
    db = conn or connect_gateway_db()
    try:
        db.execute(
            """
            INSERT INTO slack_installs
                (team_id, bot_token, bot_user_id, clerk_org_id, installed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(team_id) DO UPDATE SET
                bot_token = excluded.bot_token,
                bot_user_id = excluded.bot_user_id,
                clerk_org_id = excluded.clerk_org_id
            """,
            (team, bot_token, bot_user_id, org, time.time()),
        )
        db.commit()
    finally:
        if owns_conn:
            db.close()


class SlackInstallLookupError(RuntimeError):
    """Raised when the install catalog cannot be read."""


def get_slack_install(team_id: str) -> SlackInstall | None:
    """Return the install for ``team_id``, or None when the team has none.

    Raises :class:`SlackInstallLookupError` when the catalog itself is
    unreadable: an unavailable catalog is not the same as an absent install,
    and treating it as one would attribute the turn to the wrong owner.
    """
    team = (team_id or "").strip()
    if not team:
        return None
    try:
        return _get_from_sqlite(team)
    except sqlite3.Error as exc:
        logger.warning("[slack-installs] lookup failed for team %s", team, exc_info=True)
        raise SlackInstallLookupError(f"install lookup failed for team {team}") from exc


def _get_from_sqlite(team_id: str) -> SlackInstall | None:
    conn = connect_gateway_db()
    try:
        row = conn.execute(
            """
            SELECT team_id, bot_token, bot_user_id, clerk_org_id
            FROM slack_installs WHERE team_id = ?
            """,
            (team_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return SlackInstall(
        team_id=str(row["team_id"]),
        bot_token=str(row["bot_token"] or ""),
        bot_user_id=str(row["bot_user_id"] or ""),
        clerk_org_id=str(row["clerk_org_id"]),
    )


__all__ = [
    "SlackInstall",
    "SlackInstallLookupError",
    "get_slack_install",
    "upsert_slack_install",
]
