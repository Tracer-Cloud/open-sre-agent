"""WhatsApp alarm dispatcher for the watchdog."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

from app.cli.support.errors import OpenSREError
from app.utils.truncation import truncate
from app.utils.whatsapp_delivery import post_whatsapp_message

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_SECONDS = 300.0
_WHATSAPP_MESSAGE_LIMIT = 4096


@dataclass(frozen=True)
class WhatsAppAlarmCredentials:
    """WhatsApp Cloud API credentials for alarm dispatch."""

    access_token: str = field(repr=False)
    phone_number_id: str = field()
    to: str = field()


def load_whatsapp_credentials_from_env(
    *,
    to_override: str | None = None,
) -> WhatsAppAlarmCredentials:
    """Read WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, and WHATSAPP_DEFAULT_TO; raise on missing."""
    access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    if not access_token:
        raise OpenSREError(
            "WHATSAPP_ACCESS_TOKEN is not set.",
            suggestion=(
                "Export WHATSAPP_ACCESS_TOKEN=<your-access-token> in your environment "
                "and retry. Get a token from the Meta Developer Dashboard."
            ),
        )

    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    if not phone_number_id:
        raise OpenSREError(
            "WHATSAPP_PHONE_NUMBER_ID is not set.",
            suggestion=(
                "Export WHATSAPP_PHONE_NUMBER_ID=<your-phone-number-id> in your environment "
                "and retry. Find it in the Meta Developer Dashboard under WhatsApp > API Setup."
            ),
        )

    stripped_override = to_override.strip() if to_override else ""
    if stripped_override:
        to = stripped_override
    else:
        to = os.getenv("WHATSAPP_DEFAULT_TO", "").strip()
    if not to:
        raise OpenSREError(
            "WhatsApp recipient (to) is not set.",
            suggestion=(
                "Export WHATSAPP_DEFAULT_TO=<recipient-phone-number> in your environment "
                "or pass --to to the watchdog command and retry."
            ),
        )

    return WhatsAppAlarmCredentials(
        access_token=access_token,
        phone_number_id=phone_number_id,
        to=to,
    )


class WhatsAppAlarmDispatcher:
    """Dispatch watchdog alarms to WhatsApp with per-threshold cooldown."""

    def __init__(
        self,
        creds: WhatsAppAlarmCredentials,
        *,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._creds = creds
        self._cooldown_seconds = cooldown_seconds
        self._last_dispatched: dict[str, float] = {}
        self._lock = threading.Lock()

    def dispatch(self, threshold_name: str, message: str) -> bool:
        """Send to WhatsApp unless this threshold is in cooldown."""
        now = self._now()

        with self._lock:
            last = self._last_dispatched.get(threshold_name)
            if last is not None and (now - last) < self._cooldown_seconds:
                logger.debug(
                    "[watchdog] whatsapp alarm suppressed by cooldown: name=%s remaining=%.1fs",
                    threshold_name,
                    self._cooldown_seconds - (now - last),
                )
                return False
            self._last_dispatched[threshold_name] = now

        text = truncate(message, _WHATSAPP_MESSAGE_LIMIT, suffix="…")

        ok, error, _ = post_whatsapp_message(
            to=self._creds.to,
            text=text,
            phone_number_id=self._creds.phone_number_id,
            access_token=self._creds.access_token,
        )
        if ok:
            return True

        # Roll back the reservation on failure so transient errors don't
        # silently swallow the next real alarm.
        with self._lock:
            if self._last_dispatched.get(threshold_name) == now:
                del self._last_dispatched[threshold_name]

        logger.warning(
            "[watchdog] whatsapp alarm delivery failed: name=%s error=%s",
            threshold_name,
            error,
        )
        return False

    @staticmethod
    def _now() -> float:
        return time.monotonic()
