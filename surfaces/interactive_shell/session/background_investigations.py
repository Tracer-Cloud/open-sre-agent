"""Background-investigation session state for the REPL.

``BackgroundInvestigationRecord`` itself lives in ``platform.common`` so the
vendor notification adapters can share the contract without importing this
package. It is re-exported here because this is the import path the REPL
runtime and its tests already use.
"""

from __future__ import annotations

from dataclasses import dataclass

from platform.background_investigations.types import BackgroundInvestigationRecord


@dataclass
class BackgroundNotificationPreferences:
    """Session-scoped channel preferences for background RCA completion notifications."""

    channels: tuple[str, ...] = ()

    def set_channels(self, values: list[str]) -> None:
        cleaned: list[str] = []
        for value in values:
            normalized = value.strip().lower()
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        self.channels = tuple(cleaned)


__all__ = [
    "BackgroundInvestigationRecord",
    "BackgroundNotificationPreferences",
]
