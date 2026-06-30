"""Telegram alarm dispatcher with per-key cooldown.

Shared by features that need throttled Telegram alerts (watchdog thresholds,
Hermes incident sinks, the Telegram send-message tool). The dispatcher takes
a string key (e.g. a threshold name or incident fingerprint) and suppresses
repeat deliveries for the same key within the cooldown window.

Credential resolution lives in
:mod:`integrations.telegram.credentials`; raw transport in
:mod:`integrations.telegram.delivery`. This module owns only the
throttling + dispatch policy.
"""

from __future__ import annotations

import logging
import threading
import time

from integrations.telegram.credentials import TelegramCredentials
from integrations.telegram.delivery import (
    post_telegram_message,
    truncate_for_telegram_html,
)
from platform.common.truncation import truncate

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

        # The cooldown slot was reserved before this network call (see lock
        # block above). If ``post_telegram_message`` returns ``ok=False`` OR
        # raises, the slot stays armed for the cooldown window and the next
        # caller for the same key is silently suppressed — emit the same
        # warning in both paths so operators see the original failure
        # instead of only the suppression debug line.
        try:
            ok, error, _ = post_telegram_message(
                chat_id=self._creds.chat_id,
                text=text,
                bot_token=self._creds.bot_token,
                parse_mode=self._parse_mode,
            )
        except Exception as exc:
            logger.warning(
                "alarm delivery raised and cooldown remains armed: name=%s error=%s",
                threshold_name,
                exc,
                exc_info=True,
            )
            return False

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
