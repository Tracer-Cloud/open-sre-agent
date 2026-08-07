"""Persisted ``feed get --since`` cursor for the Buzz poller.

Telegram's ``getUpdates`` offset is server-owned — a fresh process at
``offset=0`` still receives everything the server hasn't acked. Buzz's
``feed get --since <unix ts>`` cursor is supplied by *us*, so a naive restart
would either replay all history (``since=0``) or drop messages sent during
the downtime window (``since=now``). Persisting it avoids both failure modes.

NIP-01 ``since`` is inclusive (``created_at >= since``), so the watermark
alone is not enough: events already handled at exactly that second must be
remembered too, or a restart re-dispatches completed work. ``acked_ids`` is
that per-second dedup set — only IDs at ``since``, not the full history.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR

logger = logging.getLogger(__name__)

BUZZ_CURSOR_FILE: Path = OPENSRE_HOME_DIR / "gateway" / "buzz_cursor.json"


@dataclass(frozen=True, slots=True)
class CursorState:
    """Persisted poll watermark and inclusive-second handled IDs."""

    since: int = 0
    acked_ids: frozenset[str] = frozenset()


def load_cursor() -> CursorState:
    """Return the last handled watermark and acked IDs at that second."""
    try:
        payload = json.loads(BUZZ_CURSOR_FILE.read_text())
        since = int(payload["since"])
        raw_ids = payload.get("acked_ids") or []
        if not isinstance(raw_ids, list):
            return CursorState(since=since)
        acked = frozenset(str(item) for item in raw_ids if isinstance(item, str) and item)
        return CursorState(since=since, acked_ids=acked)
    except (OSError, ValueError, KeyError, TypeError):
        return CursorState()


def save_cursor(since: int, acked_ids: set[str] | frozenset[str]) -> None:
    """Persist the watermark and handled event IDs at that inclusive second."""
    try:
        BUZZ_CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Sorted for stable diffs / readable on-disk state.
        payload = {
            "since": int(since),
            "acked_ids": sorted(acked_ids),
        }
        BUZZ_CURSOR_FILE.write_text(json.dumps(payload))
    except OSError:
        logger.warning("[buzz-gateway] failed to persist poll cursor", exc_info=True)


__all__ = ["BUZZ_CURSOR_FILE", "CursorState", "load_cursor", "save_cursor"]
