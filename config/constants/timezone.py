"""Which timezone this deployment quotes times in.

The process reports times in whatever zone its host is set to. That is the
right answer for a laptop and the wrong one for a chat bot: a gateway pod's
zone has nothing to do with where its readers sit, so a team spread across
several zones gets an answer somebody has to convert in their head before it
can be correlated with an alert. Worse, the zone is an accident of where the
pod happens to run, so two deployments of the same software disagree.

``OPENSRE_DISPLAY_TIMEZONE`` names the zone the agent speaks in — an IANA name
such as ``America/New_York``. It is a *display* setting: it changes the
timestamps in the prompt, not the process clock, not any stored value, and not
what any tool sends to a vendor API.

Unset, the process behaves exactly as it did before and reports the host zone.
Per-reader resolution is a further step and only some surfaces can support it —
Slack exposes a user's ``tz`` on ``users.info``, Discord and Telegram expose
nothing — so a configured zone is the floor either way.
"""

from __future__ import annotations

import os
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: IANA name of the zone to quote times in, e.g. ``America/New_York``.
DISPLAY_TIMEZONE_ENV: Final[str] = "OPENSRE_DISPLAY_TIMEZONE"


def resolve_display_timezone() -> ZoneInfo | None:
    """Return the configured zone, or ``None`` when the operator set none.

    ``None`` rather than a baked-in zone so a caller can tell "unset" from
    "explicitly configured" and keep reporting the host zone in the first case,
    which is what a local CLI user wants.

    An unusable value degrades to ``None`` rather than raising. A typo in a Helm
    value must not stop the process from booting, and a zone name that is valid
    on a developer's machine can still be missing from a slim container — an
    agent quoting the wrong zone is a far better outcome than one that will not
    start.
    """
    raw = os.getenv(DISPLAY_TIMEZONE_ENV, "").strip()
    if not raw:
        return None
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # ValueError covers a name the loader rejects outright (absolute paths,
        # ``..`` segments); OSError covers an unreadable tzdata directory.
        return None


__all__ = ["DISPLAY_TIMEZONE_ENV", "resolve_display_timezone"]
