"""Scheduled Slack deliveries must convert Markdown to mrkdwn before posting.

Regression: the Telegram sibling converts Markdown to its channel's format
(``markdown_to_telegram_html``), but the Slack adapter only stripped HTML tags
and posted the LLM's raw Markdown verbatim -- ``**bold**``, ``## headings``,
and ``[label](url)`` rendered literally in Slack.
"""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import patch

from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations.slack.scheduled_delivery import SlackScheduledDelivery

_MARKDOWN_MESSAGE = "## Loop report\n**3 pods failing** — see [runbook](https://example.com/rb)"
_EXPECTED_MRKDWN = "*Loop report*\n*3 pods failing* — see <https://example.com/rb|runbook>"


def _task(*, chat_id: str = "") -> ScheduledTask:
    return ScheduledTask(
        kind=TaskKind.DAILY_SUMMARY,
        cron="0 9 * * *",
        provider=Provider.SLACK,
        chat_id=chat_id,
    )


def test_webhook_delivery_converts_markdown_to_mrkdwn() -> None:
    with (
        patch(
            "integrations.slack.scheduled_delivery.resolve_slack_credentials",
            return_value={"webhook_url": "https://hooks.slack.com/services/T/B/x"},
        ),
        patch(
            "integrations.slack.scheduled_delivery.send_slack_webhook_message",
            return_value=(True, ""),
        ) as mock_hook,
    ):
        ok, error, _ts = SlackScheduledDelivery().deliver(_task(), _MARKDOWN_MESSAGE)

    assert ok is True
    assert error == ""
    sent_text = mock_hook.call_args.args[0]
    assert sent_text == _EXPECTED_MRKDWN
    assert "**" not in sent_text
    assert "##" not in sent_text


def test_bot_token_delivery_converts_markdown_to_mrkdwn() -> None:
    with (
        patch(
            "integrations.slack.scheduled_delivery.resolve_slack_credentials",
            return_value={"access_token": "xoxb-test"},
        ),
        patch("integrations.slack.scheduled_delivery.post_json") as mock_post,
    ):
        mock_post.return_value.ok = True
        mock_post.return_value.status_code = HTTPStatus.OK
        mock_post.return_value.data = {"ok": True, "ts": "123.456"}

        ok, error, ts = SlackScheduledDelivery().deliver(_task(chat_id="C123"), _MARKDOWN_MESSAGE)

    assert ok is True
    assert error == ""
    assert ts == "123.456"
    sent_payload = mock_post.call_args.kwargs["payload"]
    assert sent_payload["text"] == _EXPECTED_MRKDWN
