"""Sentry tool-usage recipe for the evidence-gather prompt.

Registered with :func:`infrastructure.harness_ports.register_gather_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def sentry_gather_prompt_fragment() -> str:
    return (
        "For Sentry overview, reliability summary, cluster, or morning-digest "
        "questions, call search_sentry_issues exactly once with query "
        '"is:unresolved" and stats_period "7d" for week/overview prompts or '
        '"24h" for today/overnight. If the result is empty, stop — do not widen '
        "stats_period or call search again with a broader window. Use "
        "digest.structural_clusters and digest.top_issues; do not repeat the "
        "same search."
    )


__all__ = ["sentry_gather_prompt_fragment"]
