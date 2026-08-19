"""Keys a tool-result payload may carry for the turn engine to read."""

from __future__ import annotations

# Whether the tool already showed its output during the call. Missing or true
# means shown, so the action closer must not reprint it.
RESULT_DISPLAYED_FIELD = "displayed"

__all__ = ["RESULT_DISPLAYED_FIELD"]
