"""SQLite persistence for gateway session bindings and Slack installs."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR

_GATEWAY_DIR = OPENSRE_HOME_DIR / "gateway"
_DEFAULT_DB_PATH = _GATEWAY_DIR / "state.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_session_bindings (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (platform, chat_id, principal_id, actor_id)
);

CREATE TABLE IF NOT EXISTS slack_installs (
    team_id TEXT PRIMARY KEY,
    bot_token TEXT NOT NULL DEFAULT '',
    bot_user_id TEXT NOT NULL DEFAULT '',
    clerk_org_id TEXT NOT NULL,
    installed_at REAL NOT NULL
);
"""


def gateway_dir() -> Path:
    return _GATEWAY_DIR


def default_gateway_db_path() -> Path:
    return _DEFAULT_DB_PATH


def _migrate_bindings_add_principal(conn: sqlite3.Connection) -> None:
    """Add principal_id to legacy bindings tables (pre-principal schema)."""
    rows = conn.execute("PRAGMA table_info(gateway_session_bindings)").fetchall()
    columns = {str(row["name"] if isinstance(row, sqlite3.Row) else row[1]) for row in rows}
    if not columns:
        return
    if "principal_id" in columns:
        return
    conn.executescript(
        """
        ALTER TABLE gateway_session_bindings RENAME TO gateway_session_bindings_legacy;
        CREATE TABLE gateway_session_bindings (
            platform TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            actor_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (platform, chat_id, principal_id, actor_id)
        );
        INSERT INTO gateway_session_bindings
            (platform, chat_id, principal_id, actor_id, session_id, updated_at)
        SELECT platform, chat_id, '', '', session_id, updated_at
        FROM gateway_session_bindings_legacy;
        DROP TABLE gateway_session_bindings_legacy;
        """
    )
    conn.commit()


def _migrate_bindings_add_actor(conn: sqlite3.Connection) -> None:
    """Add actor_id so Slack members do not share a thread session."""
    rows = conn.execute("PRAGMA table_info(gateway_session_bindings)").fetchall()
    columns = {str(row["name"] if isinstance(row, sqlite3.Row) else row[1]) for row in rows}
    if not columns or "actor_id" in columns:
        return
    conn.executescript(
        """
        ALTER TABLE gateway_session_bindings RENAME TO gateway_session_bindings_pre_actor;
        CREATE TABLE gateway_session_bindings (
            platform TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            actor_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (platform, chat_id, principal_id, actor_id)
        );
        INSERT INTO gateway_session_bindings
            (platform, chat_id, principal_id, actor_id, session_id, updated_at)
        SELECT platform, chat_id, principal_id, '', session_id, updated_at
        FROM gateway_session_bindings_pre_actor;
        DROP TABLE gateway_session_bindings_pre_actor;
        """
    )
    conn.commit()


def connect_gateway_db(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or default_gateway_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    _migrate_bindings_add_principal(conn)
    _migrate_bindings_add_actor(conn)
    return conn
