"""Typed payload shapes exchanged with the OpenClaw MCP bridge.

Aliases only — no logic, so every other module in this package (and the tool
entrypoints in ``__init__``) can import them without a cycle.
"""

from __future__ import annotations

__all__ = ["OpenClawBridgeResponse", "OpenClawConversationRow", "OpenClawParams"]

# Arguments handed to an MCP tool call, or connection overrides passed into a
# bridge tool (``openclaw_url``, ``openclaw_mode``, ...).
OpenClawParams = dict[str, object]

# Payload a bridge tool returns to the agent: always carries ``source`` and
# ``available``; the remaining keys depend on the tool.
OpenClawBridgeResponse = dict[str, object]

# One conversation record as returned by ``conversations_list``.
OpenClawConversationRow = dict[str, object]
