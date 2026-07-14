"""Slack Web API messaging client used by the gateway."""

from __future__ import annotations

import logging
from typing import Protocol

from slack_sdk.errors import SlackApiError
from slack_sdk.web import WebClient

logger = logging.getLogger(__name__)


class SlackMessagingClient(Protocol):
    """The messaging surface the Slack output sink needs."""

    def post_message(self, *, channel: str, text: str, thread_ts: str | None = None) -> str | None:
        """Post a message and return its ``ts``, or ``None`` on failure."""

    def update_message(self, *, channel: str, ts: str, text: str) -> bool:
        """Replace a posted message's text; return whether the update succeeded."""


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
