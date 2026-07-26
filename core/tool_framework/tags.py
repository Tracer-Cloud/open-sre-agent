"""Shared tool-tag constants (vendor-agnostic).

Tools opt into harness behaviors by declaring these tags — core must not
hardcode vendor tool names.
"""

from __future__ import annotations

# When set on a tool, a successful action result is stashed for the
# summarize_observation turn route (structured discovery JSON → user prose).
SUMMARIZE_OBSERVATION_TAG = "summarize_observation"

__all__ = ["SUMMARIZE_OBSERVATION_TAG"]
