"""Slack tool-usage recipe for the evidence-gather prompt.

Registered with :func:`platform.harness_ports.register_gather_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def slack_gather_prompt_fragment() -> str:
    return (
        "For Slack channel/thread summarize or history questions, call "
        "slack_read_messages with the named #channel or the channel_id from a "
        "[Slack channel_id=…] context line. For Slack workspace message search, "
        "call slack_search_messages. For who is on the team / roster / member IDs, "
        "call slack_list_team_members only — never slack_read_messages, even when "
        "a Slack channel_id context line is present."
    )


__all__ = ["slack_gather_prompt_fragment"]
