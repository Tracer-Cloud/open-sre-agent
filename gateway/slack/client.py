"""Slack Web API messaging client used by the gateway."""

from __future__ import annotations

import logging
from typing import Protocol

from slack_sdk.errors import SlackApiError
from slack_sdk.web import WebClient

logger = logging.getLogger(__name__)

_WORKING_REACTION = "eyes"
_DONE_REACTION = "white_check_mark"
_FAILED_REACTION = "x"


class SlackMessagingClient(Protocol):
    """The messaging surface the Slack output sink needs."""

    def post_message(self, *, channel: str, text: str, thread_ts: str | None = None) -> str | None:
        """Post a message and return its ``ts``, or ``None`` on failure."""

    def update_message(self, *, channel: str, ts: str, text: str) -> bool:
        """Replace a posted message's text; return whether the update succeeded."""

    def add_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        """Add an emoji reaction; return whether it succeeded."""

    def remove_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        """Remove an emoji reaction; return whether it succeeded."""


class SlackWebApiClient:
    """:class:`SlackMessagingClient` backed by the Slack Web API."""

    def __init__(self, web_client: WebClient) -> None:
        self._web_client = web_client

    def post_message(self, *, channel: str, text: str, thread_ts: str | None = None) -> str | None:
        try:
            response = self._web_client.chat_postMessage(
                channel=channel,
                text=text,
                thread_ts=thread_ts,
            )
        except SlackApiError as exc:
            logger.error("[slack-gateway] chat.postMessage failed: %s", exc.response.get("error"))
            return None
        return str(response.get("ts") or "") or None

    def update_message(self, *, channel: str, ts: str, text: str) -> bool:
        try:
            self._web_client.chat_update(channel=channel, ts=ts, text=text)
        except SlackApiError as exc:
            logger.debug("[slack-gateway] chat.update failed: %s", exc.response.get("error"))
            return False
        return True

    def add_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        try:
            self._web_client.reactions_add(channel=channel, timestamp=timestamp, name=emoji)
        except SlackApiError as exc:
            error = str(exc.response.get("error") or "")
            if error == "already_reacted":
                return True
            logger.debug("[slack-gateway] reactions.add failed: %s", error)
            return False
        return True

    def remove_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        try:
            self._web_client.reactions_remove(channel=channel, timestamp=timestamp, name=emoji)
        except SlackApiError as exc:
            error = str(exc.response.get("error") or "")
            if error == "no_reaction":
                return True
            logger.debug("[slack-gateway] reactions.remove failed: %s", error)
            return False
        return True


def mark_turn_working(client: SlackMessagingClient, *, channel: str, timestamp: str) -> None:
    """Best-effort eyes reaction while the agent is working."""
    client.add_reaction(channel=channel, timestamp=timestamp, emoji=_WORKING_REACTION)


def mark_turn_done(client: SlackMessagingClient, *, channel: str, timestamp: str) -> None:
    """Swap eyes → checkmark when the turn finishes."""
    client.remove_reaction(channel=channel, timestamp=timestamp, emoji=_WORKING_REACTION)
    client.add_reaction(channel=channel, timestamp=timestamp, emoji=_DONE_REACTION)


def mark_turn_failed(client: SlackMessagingClient, *, channel: str, timestamp: str) -> None:
    """Swap eyes → x when the turn raised without completing."""
    client.remove_reaction(channel=channel, timestamp=timestamp, emoji=_WORKING_REACTION)
    client.add_reaction(channel=channel, timestamp=timestamp, emoji=_FAILED_REACTION)
