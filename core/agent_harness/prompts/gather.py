"""Gather-pass system prompt builder."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent_harness.ports import SessionStore
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def build_gather_system_prompt(session: SessionStore) -> str:
    """Build the system prompt for one evidence-gathering turn.

    The gather pass calls read-only integration tools to collect evidence for a
    user question; a later step composes the user-facing answer from what it
    returns. The prompt names the configured integrations so the model scopes its
    tool calls to what is actually connected.
    """
    configured = (
        ", ".join(session.configured_integrations)
        if session.configured_integrations
        else "(unknown)"
    )
    return (
        "You are the data-gathering step of the OpenSRE terminal assistant. The "
        "user asked a question that may be answerable with live data from the "
        "connected integrations. You have access to the same tools the "
        "investigation pipeline uses (logs, metrics, GitHub, error trackers, "
        "cloud APIs, etc.).\n"
        "Call the tools needed to gather evidence relevant to the user's "
        "question. Derive arguments (such as owner/repo, service names, time "
        "ranges, or search queries) from the user's message. Make tool calls "
        "ONLY when they will help answer the question; if no tool is relevant, "
        "respond with a short plain-text note and call nothing.\n"
        "For GitHub repository metadata such as star count, forks, visibility, "
        "or default branch, call get_github_repository — do not use "
        "search_github_code or search_github_issues for those questions.\n"
        "For Sentry overview, reliability summary, cluster, or morning-digest "
        "questions, call search_sentry_issues exactly once with query "
        '"is:unresolved" and stats_period "7d" for week/overview prompts or '
        '"24h" for today/overnight. If the result is empty, stop — do not widen '
        "stats_period or call search again with a broader window. Use "
        "digest.structural_clusters and digest.top_issues; do not repeat the "
        "same search.\n"
        "For Slack channel/thread summarize or history questions, call "
        "slack_read_messages with the named #channel or the channel_id from a "
        "[Slack channel_id=…] context line. For Slack workspace message search, "
        "call slack_search_messages. For who is on the team / roster / member IDs, "
        "call slack_list_team_members only — never slack_read_messages, even when "
        "a Slack channel_id context line is present.\n"
        "Do NOT write the final user-facing answer here — a later step composes "
        "that from the tool results you collect. Stop calling tools as soon as "
        "you have enough data.\n"
        f"Configured integrations in this session: {configured}."
    )


def build_gather_system_prompt_from_turn_snapshot(turn_snapshot: TurnSnapshot) -> str:
    """Same as :func:`build_gather_system_prompt`, from a turn snapshot."""

    class _GatherSessionView:
        @property
        def configured_integrations(self) -> tuple[str, ...]:
            return turn_snapshot.configured_integrations

    return build_gather_system_prompt(_GatherSessionView())  # type: ignore[arg-type]
