"""Telegram alarm dispatcher with per-key cooldown.

Shared by features that need throttled Telegram alerts (watchdog thresholds,
Hermes incident sinks, the Telegram send-message tool). The dispatcher takes
a string key (e.g. a threshold name or incident fingerprint) and suppresses
repeat deliveries for the same key within the cooldown window.

Credential resolution lives in
:mod:`platform.notifications.telegram_credentials`; raw transport in
:mod:`platform.notifications.telegram_delivery`. This module owns only the
throttling + dispatch policy.
"""

from __future__ import annotations

import logging
import threading
import time

from platform.common.truncation import truncate
from platform.notifications.telegram_credentials import TelegramCredentials
from platform.notifications.telegram_delivery import (
    post_telegram_message,
    truncate_for_telegram_html,
)

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_SECONDS = 300.0
_TELEGRAM_MESSAGE_LIMIT = 4096


class AlarmDispatcher:
    """Dispatch Telegram alarms with per-key cooldown."""

    def __init__(
        self,
        creds: TelegramCredentials,
        *,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
        parse_mode: str = "",
    ) -> None:
        self._creds = creds
        self._cooldown_seconds = cooldown_seconds
        self._parse_mode = parse_mode
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
                    "alarm suppressed by cooldown: name=%s remaining=%.1fs",
                    threshold_name,
                    self._cooldown_seconds - (now - last),
                )
                return False
            self._last_dispatched[threshold_name] = now

        if self._parse_mode.upper() == "HTML":
            text = truncate_for_telegram_html(message, _TELEGRAM_MESSAGE_LIMIT, suffix="…")
        else:
            text = truncate(message, _TELEGRAM_MESSAGE_LIMIT, suffix="…")

        ok, error, _ = post_telegram_message(
            chat_id=self._creds.chat_id,
            text=text,
            bot_token=self._creds.bot_token,
            parse_mode=self._parse_mode,
        )
        if ok:
            return True

        logger.warning(
            "alarm delivery failed and cooldown remains armed: name=%s error=%s",
            threshold_name,
            error,
        )
        return False

    @staticmethod
    def _now() -> float:
        return time.monotonic()
