"""Constants shared between orchestration routing and investigation stages."""

from __future__ import annotations

from typing import Final

MAX_INVESTIGATION_LOOPS = 20

# Internal availability-view entry that carries alert data to tool parameter
# extractors without coupling shared investigation orchestration to a vendor.
INVESTIGATION_CONTEXT_SOURCE_KEY: Final[str] = "_investigation_context"

# Approval tokens auto-expire after this many seconds (5 minutes).
DEFAULT_APPROVAL_EXPIRY_SECONDS: Final[int] = 300

__all__ = [
    "DEFAULT_APPROVAL_EXPIRY_SECONDS",
    "INVESTIGATION_CONTEXT_SOURCE_KEY",
    "MAX_INVESTIGATION_LOOPS",
]
