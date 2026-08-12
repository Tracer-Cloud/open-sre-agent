"""PostHog tool-usage recipe for the evidence-gather prompt.

Registered with :func:`platform.harness_ports.register_gather_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def posthog_gather_prompt_fragment() -> str:
    return (
        "For PostHog metric, user-count, or HogQL questions: do not probe "
        'entity taxonomy with invented kinds like "events" and do not stop '
        "after list_posthog_tools or schema errors. Prefer call_posthog_tool "
        "with tool_name='execute-sql' and a HogQL count (FROM events, filter "
        "properties.$os / $browser as needed), or tool_name='query-trends' "
        "when a trends series fits. At most one list_posthog_tools call, then "
        "run the metric query — a failed schema probe is not an answer."
    )


__all__ = ["posthog_gather_prompt_fragment"]
