"""Slack-reactions provider for the investigation intake and delivery flows.

Both ``tools/investigation/stages/intake/node.py`` (which flips an eyes/check
reaction to signal that a Slack-triggered investigation started or was
classified as noise) and the Slack delivery adapter itself update a message's
emoji when the report is posted. Both used to call
``integrations.slack.delivery.{add,swap}_reaction`` directly, which forms a
``tools -> integrations`` layering edge (T-4 audit, issue #3352).

This module owns the neutral seam:

* :class:`SlackReactionsProvider` — the small protocol the investigation code
  talks to.
* :func:`register_slack_reactions_provider` — the Slack integration adapter calls
  this at import time to advertise its concrete implementation.
* :func:`get_slack_reactions_provider` — investigation code retrieves the provider
  and treats a missing provider as "reactions are not wired for this run".

Registration is process-scoped. Tests may pass ``None`` to clear the provider,
or bind a stub implementation to verify emoji transitions without hitting the
Slack API.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SlackReactionsProvider(Protocol):
    """Abstract Slack reaction operations used by the investigation flow.

    Concrete implementations live in the Slack integration package. The two
    operations mirror ``chat.postMessage``-adjacent Slack Web API calls used
    to nudge users about investigation progress.
    """

    def add_reaction(self, emoji: str, channel: str, timestamp: str, token: str) -> None:
        """Add ``emoji`` on the message identified by ``channel`` / ``timestamp``."""

    def swap_reaction(
        self,
        remove_emoji: str,
        add_emoji: str,
        channel: str,
        timestamp: str,
        token: str,
    ) -> None:
        """Replace ``remove_emoji`` with ``add_emoji`` on the target message."""


_provider: SlackReactionsProvider | None = None


def register_slack_reactions_provider(provider: SlackReactionsProvider | None) -> None:
    """Bind (or clear) the concrete Slack reactions provider.

    Passing ``None`` clears the provider — used in tests that need to assert the
    default no-reactions-wired branch. The Slack integration adapter registers
    the real implementation at ``integrations.slack.reporting_adapter`` import
    time.
    """
    global _provider
    _provider = provider


def get_slack_reactions_provider() -> SlackReactionsProvider | None:
    """Return the currently registered provider, or ``None`` when unset."""
    return _provider


__all__ = [
    "SlackReactionsProvider",
    "get_slack_reactions_provider",
    "register_slack_reactions_provider",
]
