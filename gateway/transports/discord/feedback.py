"""Reply feedback buttons on Discord final answers."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import discord

from config.constants import OPENSRE_HOME_DIR

logger = logging.getLogger("gateway")

FEEDBACK_GOOD_ID = "opensre_reply_feedback:good"
FEEDBACK_BAD_ID = "opensre_reply_feedback:bad"

_DEFAULT_FEEDBACK_PATH = OPENSRE_HOME_DIR / "gateway" / "discord_feedback.jsonl"
_WRITE_LOCK = threading.Lock()


def feedback_components() -> list[dict[str, Any]]:
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 2,
                    "label": "Helpful",
                    "custom_id": FEEDBACK_GOOD_ID,
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Not helpful",
                    "custom_id": FEEDBACK_BAD_ID,
                },
            ],
        }
    ]


def record_feedback_interaction(interaction: discord.Interaction) -> bool:
    if interaction.type != discord.InteractionType.component:
        return False
    data = interaction.data
    if not isinstance(data, dict):
        return False
    custom_id = str(data.get("custom_id") or "")
    if custom_id == FEEDBACK_GOOD_ID:
        verdict = "good"
    elif custom_id == FEEDBACK_BAD_ID:
        verdict = "bad"
    else:
        return False
    message = interaction.message
    entry = {
        "ts": time.time(),
        "platform": "discord",
        "user_id": str(interaction.user.id),
        "channel_id": str(interaction.channel_id or ""),
        "message_id": str(message.id) if message is not None else "",
        "verdict": verdict,
    }
    if not _append_jsonl(entry, path=_DEFAULT_FEEDBACK_PATH):
        return False
    logger.info(
        "[discord-gateway] reply feedback verdict=%s channel=%s",
        verdict,
        entry["channel_id"],
    )
    return True


def _append_jsonl(entry: dict[str, Any], *, path: Path) -> bool:
    try:
        with _WRITE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("[discord-gateway] feedback write failed path=%s", path, exc_info=True)
        return False
    return True


__all__ = ["feedback_components", "record_feedback_interaction"]
