"""Buzz alarm dispatcher with per-key cooldown.

Shared by features that need throttled Buzz alerts (watchdog thresholds) —
mirrors :class:`integrations.rocketchat.alarms.RocketChatAlarmDispatcher`.

Credential resolution lives in :mod:`integrations.buzz.credentials`; raw
transport in :mod:`integrations.buzz.delivery`. This module owns only the
throttling + dispatch policy.
"""

from __future__ import annotations

import logging
import time

from integrations.buzz.credentials import BuzzCredentials
from integrations.buzz.delivery import post_buzz_message
from platform.common.truncation import truncate
from platform.notifications.cooldown import CooldownGate
from platform.notifications.limits import MAX_MESSAGE_SIZE

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_SECONDS = 300.0
_BUZZ_MESSAGE_LIMIT = MAX_MESSAGE_SIZE


class BuzzAlarmDispatcher:
    """Dispatch Buzz alarms with per-key cooldown."""

    def __init__(
        self,
        creds: BuzzCredentials,
        *,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._creds = creds
        self._gate = CooldownGate(cooldown_seconds)

    def dispatch(self, threshold_name: str, message: str) -> bool:
        """Send to Buzz unless this threshold is in cooldown."""
        now = self._now()

        remaining = self._gate.try_reserve(threshold_name, now)
        if remaining is not None:
            logger.debug(
                "alarm suppressed by cooldown: name=%s remaining=%.1fs",
                threshold_name,
                remaining,
            )
            return False

        text = truncate(message, _BUZZ_MESSAGE_LIMIT, suffix="…")

        # The cooldown slot was reserved before this network call. If the
        # delivery returns ok=False OR raises, the slot stays armed for the
        # cooldown window and the next caller for the same key is silently
        # suppressed — emit the same warning in both paths so operators see
        # the original failure instead of only the suppression debug line.
        try:
            ok, error, _event_id = post_buzz_message(
                self._creds.relay_url,
                self._creds.channel,
                text,
                self._creds.private_key,
                auth_tag=self._creds.auth_tag,
                buzz_path=self._creds.buzz_path,
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


__all__ = ["BuzzAlarmDispatcher"]
