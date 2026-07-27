"""SQLite persistence for gateway session bindings and Slack installs.

Two databases, because the two tables have opposite scopes:

- ``slack_installs`` maps a Slack team to its organization, so it must be
  readable *before* the organization is known. It stays on the host root.
- ``gateway_session_bindings`` maps a conversation to a session inside one
  organization. It lives on that organization's context root, which in a
  deployed service is the mounted volume, so a replaced task still resolves
  a thread to the transcript it already has.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

from config.constants.paths import host_home, opensre_home

logger = logging.getLogger(__name__)

_GATEWAY_DIR_NAME = "gateway"
_INSTALLS_DB_NAME = "state.db"
_BINDINGS_DB_NAME = "bindings.db"

_INSTALLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS slack_installs (
    team_id TEXT PRIMARY KEY,
    bot_token TEXT NOT NULL DEFAULT '',
    bot_user_id TEXT NOT NULL DEFAULT '',
    clerk_org_id TEXT NOT NULL,
    installed_at REAL NOT NULL
);
"""

_BINDINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_session_bindings (
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (platform, chat_id, principal_id, actor_id)
);
"""

_BINDINGS_COLUMNS = "platform, chat_id, principal_id, actor_id, session_id, updated_at"


def gateway_dir() -> Path:
    """Host directory for gateway process state (pidfile, logs, install catalog)."""
    return host_home() / _GATEWAY_DIR_NAME


def default_gateway_db_path() -> Path:
    """Path of the host-level install catalog."""
    return gateway_dir() / _INSTALLS_DB_NAME


def bindings_db_path() -> Path:
    """Path of the current organization's session bindings."""
    return opensre_home() / _GATEWAY_DIR_NAME / _BINDINGS_DB_NAME


def _open(path: Path, schema: str) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(schema)
    conn.commit()
    return conn


def connect_gateway_db(path: Path | None = None) -> sqlite3.Connection:
    """Open the host-level install catalog."""
    return _open(path or default_gateway_db_path(), _INSTALLS_SCHEMA)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"] if isinstance(row, sqlite3.Row) else row[1]) for row in rows}


def _read_legacy_rows(legacy_path: Path) -> list[tuple[str, str, str, str, str, float]]:
    """Read bindings from an older database, whatever columns it happens to have.

    Databases written before scoping have only
    ``(platform, chat_id, session_id, updated_at)``; selecting the newer columns
    outright would raise and silently drop every conversation. Missing ids
    default to the unscoped empty value, which is what those rows meant.
    """
    if not legacy_path.is_file():
        return []
    legacy = sqlite3.connect(legacy_path)
    legacy.row_factory = sqlite3.Row
    try:
        columns = _table_columns(legacy, "gateway_session_bindings")
        if not {"platform", "chat_id", "session_id", "updated_at"} <= columns:
            return []
        principal = "principal_id" if "principal_id" in columns else "''"
        actor = "actor_id" if "actor_id" in columns else "''"
        rows = legacy.execute(
            f"SELECT platform, chat_id, {principal}, {actor}, session_id, updated_at "
            "FROM gateway_session_bindings"
        ).fetchall()
    except sqlite3.Error:
        logger.warning("[gateway] could not read legacy bindings at %s", legacy_path, exc_info=True)
        return []
    finally:
        legacy.close()
    return [
        (str(r[0]), str(r[1]), str(r[2] or ""), str(r[3] or ""), str(r[4]), float(r[5]))
        for r in rows
    ]


def _adopt_legacy_bindings(conn: sqlite3.Connection, legacy_paths: Sequence[Path]) -> None:
    """Copy bindings written before they moved off the host database.

    Without this a laptop, a Telegram gateway, or a silo cutting over to a
    mounted volume would drop every existing conversation on first open.
    """
    if conn.execute("SELECT 1 FROM gateway_session_bindings LIMIT 1").fetchone() is not None:
        return
    rows: list[tuple[str, str, str, str, str, float]] = []
    for legacy_path in legacy_paths:
        rows.extend(_read_legacy_rows(legacy_path))
    if not rows:
        return
    conn.executemany(
        f"INSERT OR IGNORE INTO gateway_session_bindings ({_BINDINGS_COLUMNS}) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    logger.info("[gateway] adopted %d session binding(s) from a previous layout", len(rows))


def connect_bindings_db(path: Path | None = None) -> sqlite3.Connection:
    """Open the organization's session bindings, adopting any legacy rows.

    Two places may hold pre-split rows: the database beside this one, and the
    host database a silo used before its context moved onto a mounted volume.
    """
    db_path = path or bindings_db_path()
    conn = _open(db_path, _BINDINGS_SCHEMA)
    candidates = [db_path.parent / _INSTALLS_DB_NAME, default_gateway_db_path()]
    _adopt_legacy_bindings(conn, [p for p in dict.fromkeys(candidates) if p != db_path])
    return conn


class BindingsConnections:
    """Cache of bindings connections, one per resolved database path.

    The path follows the organization bound for the turn, so it is resolved on
    use rather than captured when the worker starts.
    """

    def __init__(self, resolve_path: Callable[[], Path] = bindings_db_path) -> None:
        self._resolve_path = resolve_path
        self._by_path: dict[str, sqlite3.Connection] = {}

    def __call__(self) -> sqlite3.Connection:
        key = str(self._resolve_path())
        conn = self._by_path.get(key)
        if conn is None:
            conn = connect_bindings_db(Path(key))
            self._by_path[key] = conn
        return conn

    def close(self) -> None:
        for conn in self._by_path.values():
            conn.close()
        self._by_path.clear()


__all__ = [
    "BindingsConnections",
    "bindings_db_path",
    "connect_bindings_db",
    "connect_gateway_db",
    "default_gateway_db_path",
    "gateway_dir",
]
