"""Constants shared between orchestration routing and investigation stages."""

from __future__ import annotations

from typing import Final

MAX_INVESTIGATION_LOOPS = 20

# Approval tokens auto-expire after this many seconds (5 minutes).
DEFAULT_APPROVAL_EXPIRY_SECONDS: Final[int] = 300

__all__ = ["DEFAULT_APPROVAL_EXPIRY_SECONDS", "MAX_INVESTIGATION_LOOPS"]
