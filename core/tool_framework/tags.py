"""Shared tool-tag constants (vendor-agnostic).

Tools opt into harness behaviors by declaring these tags — core must not
hardcode vendor tool names.
"""

from __future__ import annotations

# When set on a tool, a successful action result is stashed for the
# summarize_observation turn route (structured discovery JSON → user prose).
SUMMARIZE_OBSERVATION_TAG = "summarize_observation"

# Marks a tool as a deterministic last-resort investigation action: eligible
# for selection only when no other tool scores positively for the alert.
FALLBACK_PLANNING_TAG = "fallback_planning"

# Marks a tool's source as a generic fallback source (useful, but never
# primary when incident-specific integrations match). See
# core.domain.alerts.alert_source.secondary_tool_sources.
SECONDARY_SOURCE_TAG = "secondary_source"

__all__ = ["FALLBACK_PLANNING_TAG", "SECONDARY_SOURCE_TAG", "SUMMARIZE_OBSERVATION_TAG"]
