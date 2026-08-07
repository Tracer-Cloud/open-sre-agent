"""Persisted ``feed get --since`` cursor for the Buzz poller.

Telegram's ``getUpdates`` offset is server-owned — a fresh process at
``offset=0`` still receives everything the server hasn't acked. Buzz's
``feed get --since <unix ts>`` cursor is supplied by *us*, so a naive restart
would either replay all history (``since=0``) or drop messages sent during
the downtime window (``since=now``). Persisting it avoids both failure modes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR

logger = logging.getLogger(__name__)

BUZZ_CURSOR_FILE: Path = OPENSRE_HOME_DIR / "gateway" / "buzz_cursor.json"


def load_cursor() -> int:
    """Return the last-processed event timestamp, or 0 when there is none yet."""
    try:
        payload = json.loads(BUZZ_CURSOR_FILE.read_text())
        return int(payload["since"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def save_cursor(since: int) -> None:
    """Persist the cursor after a successfully-processed poll batch."""
    try:
        BUZZ_CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
        BUZZ_CURSOR_FILE.write_text(json.dumps({"since": since}))
    except OSError:
        logger.warning("[buzz-gateway] failed to persist poll cursor", exc_info=True)


__all__ = ["BUZZ_CURSOR_FILE", "load_cursor", "save_cursor"]
