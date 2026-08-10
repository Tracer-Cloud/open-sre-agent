"""How verbose this deployment's process logging is.

The gateway pins the root logger to INFO at boot, which is the right default for
a long-running service but leaves no way to turn detail on without shipping a
new image. That matters most for the turn paths that degrade quietly: the gather
evidence pass has several exits that end a turn with a text-only answer, and
anything they log below the pinned level is invisible in production — an
operator sees a bot that "just didn't use its tools" and nothing to explain why.

``OPENSRE_LOG_LEVEL`` is the one knob, and it is purely additive: unset, the
process configures logging exactly as it did before.
"""

from __future__ import annotations

import logging
import os
from typing import Final

#: Overrides the process-wide log level, e.g. ``DEBUG``.
LOG_LEVEL_ENV: Final[str] = "OPENSRE_LOG_LEVEL"


def resolve_log_level() -> int | None:
    """Return the configured level, or ``None`` when the operator set none.

    ``None`` rather than a baked-in default so a caller can tell "unset" from
    "explicitly INFO" and leave its own configuration untouched in the first
    case. A blank or unrecognized value is treated as unset rather than raising:
    a typo in a Helm value must not stop the process from booting, and a service
    that will not start is a worse outcome than one that logs at its default.
    """
    raw = os.getenv(LOG_LEVEL_ENV, "").strip().upper()
    if not raw:
        return None
    # ``getLevelName`` returns the int for a known name and the string
    # ``"Level <name>"`` for anything else, which is the documented way to test
    # a level name without reaching for a private lookup table.
    level = logging.getLevelName(raw)
    return level if isinstance(level, int) else None


__all__ = ["LOG_LEVEL_ENV", "resolve_log_level"]
