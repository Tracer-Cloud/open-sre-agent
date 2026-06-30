"""SQLite persistence for gateway session bindings."""

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
    session_id TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (platform, chat_id)
);
"""


def connect_gateway_db(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or _DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn
