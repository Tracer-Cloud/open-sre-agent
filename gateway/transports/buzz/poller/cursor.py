"""Persisted ``feed get --since`` cursor for the Buzz poller.

Telegram's ``getUpdates`` offset is server-owned — a fresh process at
``offset=0`` still receives everything the server hasn't acked. Buzz's
``feed get --since <unix ts>`` cursor is supplied by *us*, so a naive restart
would either replay all history (``since=0``) or drop messages sent during
the downtime window (``since=now``). Persisting it avoids both failure modes.

Two facts make a bare watermark insufficient, and both are covered by
persisting the handled IDs *with* it:

* NIP-01 ``since`` is inclusive (``created_at >= since``), so events already
  handled at exactly that second must be remembered or a restart re-runs them.
* Turns run concurrently and finish out of order, so a newer event can be
  handled while an older one is still in flight. The watermark cannot advance
  past the older one, which leaves the newer completion recorded nowhere but
  this file.

So one map is persisted, not a watermark plus a special case: every handled
event at or above ``since``. Everything strictly below ``since`` is handled by
definition and needs no IDs, which is what keeps the file small.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR

logger = logging.getLogger(__name__)

BUZZ_CURSOR_FILE: Path = OPENSRE_HOME_DIR / "gateway" / "buzz_cursor.json"


@dataclass(frozen=True, slots=True)
class CursorState:
    """Poll watermark plus every handled event ID at or above it."""

    since: int = 0
    handled: dict[str, int] = field(default_factory=dict)


def load_cursor() -> CursorState:
    """Return the persisted watermark and handled events at or above it."""
    try:
        payload = json.loads(BUZZ_CURSOR_FILE.read_text())
        since = int(payload["since"])
    except (OSError, ValueError, KeyError, TypeError):
        # A missing or corrupt cursor replays from scratch rather than
        # skipping: duplicated work is recoverable, dropped mentions are not.
        return CursorState()

    handled: dict[str, int] = {}
    raw_handled = payload.get("handled")
    if isinstance(raw_handled, dict):
        for event_id, created_at in raw_handled.items():
            if isinstance(event_id, str) and event_id and isinstance(created_at, int):
                handled[event_id] = created_at
    else:
        # Pre-concurrency format: a flat ID list, all of them at ``since``.
        raw_ids = payload.get("acked_ids")
        if isinstance(raw_ids, list):
            handled = {item: since for item in raw_ids if isinstance(item, str) and item}
    return CursorState(since=since, handled=handled)


def save_cursor(since: int, handled: dict[str, int]) -> None:
    """Persist the watermark and handled event IDs at or above it.

    Written atomically: a torn write would read back as a corrupt cursor and
    replay the whole feed, which is exactly what the file exists to prevent.
    """
    try:
        BUZZ_CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"since": int(since), "handled": dict(handled)}
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=BUZZ_CURSOR_FILE.parent,
            prefix=f".{BUZZ_CURSOR_FILE.name}.",
            delete=False,
        ) as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
            staged = Path(handle.name)
        os.replace(staged, BUZZ_CURSOR_FILE)
    except OSError:
        logger.warning("[buzz-gateway] failed to persist poll cursor", exc_info=True)


__all__ = ["BUZZ_CURSOR_FILE", "CursorState", "load_cursor", "save_cursor"]
