"""Background RCA notification entry point for the REPL runtime.

Channel selection lives in ``platform.notifications``. This runs the registration
bootstrap first, which ``platform`` cannot do itself because ``bootstrap`` sits
above it. Imports stay function-local so the REPL boot path pays for none of it.
"""

from __future__ import annotations

from platform.background_investigations.types import BackgroundInvestigationRecord


def deliver_background_notifications(
    *,
    record: BackgroundInvestigationRecord,
    channels: tuple[str, ...],
) -> dict[str, str]:
    """Send configured notifications for a completed background RCA."""
    from bootstrap.adapters import install_notification_adapters
    from platform.notifications.outbound_dispatch import dispatch_background_notifications

    install_notification_adapters()
    return dispatch_background_notifications(record=record, channels=channels)
