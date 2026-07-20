"""PostHog conversational-assistant prompt fragment - metric report formatting rule.

Registered with :func:`platform.harness_ports.register_assistant_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def posthog_assistant_prompt_fragment() -> str:
    return (
        "PostHog metric report: open **I found:** with a one-line scope summary "
        "(window, project, metric count). Present a per-metric table with columns "
        "Metric | Current | Previous | Change | % | Trend. Use up/down/flat words for "
        "the trend, never colour alone. Call out the 1-3 most notable movers with a "
        "short why-it-matters line. When a metric window returns no data, say so "
        "explicitly for that metric, do not silently widen the window or present a "
        "failed query as zero."
    )


__all__ = ["posthog_assistant_prompt_fragment"]
