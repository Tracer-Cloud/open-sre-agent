"""PostHog MCP tool-usage recipe for the evidence-gather prompt.

Registered with :func:`platform.harness_ports.register_gather_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def posthog_mcp_gather_prompt_fragment() -> str:
    return (
        "For PostHog questions (users, events, insights, feature flags, "
        "errors), go straight to call_posthog_tool with tool_name "
        "'execute-sql' and a HogQL query — counts and aggregations do not "
        "need discovery first. Example (unique Windows users, 30 days): "
        "SELECT count(DISTINCT person_id) FROM events WHERE "
        "properties['$os'] = 'Windows' AND timestamp > now() - INTERVAL 30 "
        "DAY. Call list_posthog_tools only when execute-sql cannot answer, "
        "always with a name_filter, and at most once per turn; never re-list "
        "tools already named earlier in the conversation."
    )


__all__ = ["posthog_mcp_gather_prompt_fragment"]
