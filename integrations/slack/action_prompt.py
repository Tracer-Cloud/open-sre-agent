"""Slack action-agent prompt fragment — routes Slack teammate requests to tools.

Registered with :func:`platform.harness_ports.register_action_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def slack_action_prompt_fragment() -> str:
    return """SLACK TEAMMATE REQUESTS ARE ACTION TOOLS — NOT HANDOFFS:
When the user asks to read, summarize, search, join, react, list members, reply
in, or capture a task from Slack / a #channel / a thread, call the matching
slack_* tool directly. Do NOT emit assistant_handoff for these — they are NOT
docs questions and are NOT covered by the DATA-RETRIEVAL handoff rule (that rule
is for Datadog/Grafana/Sentry/PostHog record lookups via the gather loop).
If the message includes a line like `[Slack channel_id=C… thread_ts=…]`, use
that channel_id (and thread_ts when reading a thread) as the default target when
the user says "this channel", "here", "this thread", or "the conversation".
That context line does NOT mean "read the channel" for every Slack question —
roster / people questions ignore channel_id and call slack_list_team_members.
Examples:
* "read the last 10 messages in #opensre-slack-testing and summarize"
  → slack_read_messages(channel="#opensre-slack-testing", limit=10)
* "sum / summarize this channel's conversation" with Slack channel_id context
  → slack_read_messages(channel="C…", limit=50) using the context channel_id
* "search Slack for deploy freeze" → slack_search_messages(query="deploy freeze")
* "who is on the team?" / "who's on the team" / "list team members" / "who are
  the members?" — even when `[Slack channel_id=…]` is present
  → slack_list_team_members ONLY (never slack_read_messages, never hand off
  asking which team). Bot token tools resolve credentials themselves; do NOT
  gate on CONNECTED INTEGRATIONS.
After the tool returns, the turn summarizes the tool output — do not hand off
first asking for "target system" or `/integrations setup slack`."""


__all__ = ["slack_action_prompt_fragment"]
