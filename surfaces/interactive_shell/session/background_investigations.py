"""Background-investigation session state for the REPL.

``BackgroundInvestigationRecord`` itself lives under ``platform.scheduling`` so the
vendor notification adapters can share the contract without importing this
package. It is re-exported here because this is the import path the REPL
runtime and its tests already use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from platform.scheduling.background_investigations.types import BackgroundInvestigationRecord

logger = logging.getLogger(__name__)


@dataclass
class BackgroundNotificationPreferences:
    """Channel preferences for background RCA completion notifications.

    A value object. :meth:`set_channels` only updates it; the slash command
    persists explicitly so a write failure can be reported to the user.
    """

    channels: tuple[str, ...] = ()

    def set_channels(self, values: list[str]) -> None:
        cleaned: list[str] = []
        for value in values:
            normalized = value.strip().lower()
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        self.channels = tuple(cleaned)

    @classmethod
    def load(cls) -> BackgroundNotificationPreferences:
        """Channels a previous session persisted, or empty.

        Never raises. A damaged document, or a context root this turn may not
        read, must not stop the shell from starting; the user sees the same
        empty default they had before preferences were durable.
        """
        from platform.scheduling.background_investigations.store import (
            background_investigation_store,
        )

        try:
            return cls(channels=background_investigation_store().notify_channels())
        except Exception:  # noqa: BLE001
            logger.debug("[background] could not load notify channels", exc_info=True)
            return cls()


__all__ = [
    "BackgroundInvestigationRecord",
    "BackgroundNotificationPreferences",
]
