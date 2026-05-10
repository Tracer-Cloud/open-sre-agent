"""Telegram alarm dispatcher for the watchdog."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

from app.cli.support.errors import OpenSREError
from app.utils.telegram_delivery import post_telegram_message
from app.utils.truncation import truncate

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_SECONDS = 300.0
_TELEGRAM_MESSAGE_LIMIT = 4096


@dataclass(frozen=True)
class AlarmCredentials:
    # repr=False so the auto-generated __repr__ does not leak the token into
    # pytest assertion output, tracebacks, or structured log capture.
    bot_token: str = field(repr=False)
    chat_id: str = field()


def load_credentials_from_env(
    *,
    chat_id_override: str | None = None,
) -> AlarmCredentials:
    """Read TELEGRAM_BOT_TOKEN and TELEGRAM_DEFAULT_CHAT_ID; raise on missing."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise OpenSREError(
            "TELEGRAM_BOT_TOKEN is not set.",
            suggestion=(
                "Export TELEGRAM_BOT_TOKEN=<your-bot-token> in your environment "
                "and retry. Get a token from @BotFather on Telegram."
            ),
        )

    # Strip first so a whitespace-only override falls back to env consistently
    # with an empty-string override, instead of raising a misleading
    # "pass --chat-id" error after the caller already passed one.
    stripped_override = chat_id_override.strip() if chat_id_override else ""
    if stripped_override:
        chat_id = stripped_override
    else:
        chat_id = os.getenv("TELEGRAM_DEFAULT_CHAT_ID", "").strip()
    if not chat_id:
        raise OpenSREError(
            "Telegram chat id is not set.",
            suggestion=(
                "Export TELEGRAM_DEFAULT_CHAT_ID=<chat-id> in your environment "
                "or pass --chat-id to the watchdog command and retry."
            ),
        )

    return AlarmCredentials(bot_token=bot_token, chat_id=chat_id)


class AlarmDispatcher:
    """Dispatch watchdog alarms to Telegram with per-threshold cooldown."""

    def __init__(
        self,
        creds: AlarmCredentials,
        *,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._creds = creds
        self._cooldown_seconds = cooldown_seconds
        self._last_dispatched: dict[str, float] = {}
        self._lock = threading.Lock()

    def dispatch(self, threshold_name: str, message: str) -> bool:
        """Send to Telegram unless this threshold is in cooldown."""
        now = self._now()

        # Reserve the cooldown slot under the lock BEFORE the network call so
        # a concurrent dispatch on the same threshold sees the reservation and
        # is suppressed. Without this, two threads could both pass the check
        # (state of last_dispatched at "check" time != "use" time, classic
        # TOCTOU) and both send.
        with self._lock:
            last = self._last_dispatched.get(threshold_name)
            if last is not None and (now - last) < self._cooldown_seconds:
                logger.debug(
                    "[watchdog] alarm suppressed by cooldown: name=%s remaining=%.1fs",
                    threshold_name,
                    self._cooldown_seconds - (now - last),
                )
                return False
            self._last_dispatched[threshold_name] = now

        ok, error, _ = post_telegram_message(
            chat_id=self._creds.chat_id,
            text=truncate(message, _TELEGRAM_MESSAGE_LIMIT, suffix="…"),
            bot_token=self._creds.bot_token,
        )
        if ok:
            return True

        # Roll back the reservation only if it's still ours, so a transient
        # failure does not silently swallow the next real alarm. Compare-and-
        # delete prevents stomping on a parallel successful dispatch that
        # may have updated the slot in the meantime.
        with self._lock:
            if self._last_dispatched.get(threshold_name) == now:
                del self._last_dispatched[threshold_name]

        logger.warning(
            "[watchdog] alarm delivery failed: name=%s error=%s",
            threshold_name,
            error,
        )
        return False

    @staticmethod
    def _now() -> float:
        return time.monotonic()
