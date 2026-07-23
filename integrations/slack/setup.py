"""What Slack needs before it is considered configured.

Slack accepts an incoming webhook URL *or* Socket Mode tokens (bot + app). A
picker chooses which of the two (or both) to configure, and the ``validate``
hook still enforces the real rule for whatever the collection surface submits.
Picking one mode clears the other's fields — choose "Both" to run both at once.

The webhook URL embeds its own secret, so — like Rocket.Chat's webhook — it
stays store-only. Socket Mode tokens are mirrored to the keyring.
"""

from __future__ import annotations

from config.constants.slack import SLACK_APP_TOKEN_ENV, SLACK_BOT_TOKEN_ENV
from integrations.setup_flow import IntegrationSetupSpec, SetupField, SetupMode
from integrations.slack.verifier import verify_slack

WEBHOOK_URL_FIELD = "webhook_url"
BOT_TOKEN_FIELD = "bot_token"
APP_TOKEN_FIELD = "app_token"


def _require_webhook_or_socket_tokens(credentials: dict[str, str | None]) -> str:
    """Accept a webhook URL, or both Socket Mode tokens — not neither."""
    webhook = credentials.get(WEBHOOK_URL_FIELD)
    bot = credentials.get(BOT_TOKEN_FIELD)
    app = credentials.get(APP_TOKEN_FIELD)
    if webhook:
        return ""
    if bot and app:
        if not str(bot).startswith("xoxb-"):
            return "bot_token must start with xoxb-"
        if not str(app).startswith("xapp-"):
            return "app_token must start with xapp-"
        return ""
    return "Provide a webhook URL, or both a bot token (xoxb-) and an app token (xapp-)."


SLACK_SETUP = IntegrationSetupSpec(
    service="slack",
    fields=(
        SetupField(
            name=WEBHOOK_URL_FIELD,
            label="Slack webhook URL",
            prompt="Slack webhook URL",
            # Store-only: the URL embeds its secret.
            required=False,
            secret=True,
        ),
        SetupField(
            name=BOT_TOKEN_FIELD,
            label="Slack bot token",
            prompt="Slack bot token (xoxb-…)",
            env_var=SLACK_BOT_TOKEN_ENV,
            required=False,
            secret=True,
        ),
        SetupField(
            name=APP_TOKEN_FIELD,
            label="Slack app-level token",
            prompt="Slack app-level token (xapp-…)",
            env_var=SLACK_APP_TOKEN_ENV,
            required=False,
            secret=True,
        ),
    ),
    mode_prompt="Slack setup:",
    modes=(
        SetupMode(
            value="webhook",
            label="Incoming webhook (outbound delivery)",
            fields=(WEBHOOK_URL_FIELD,),
        ),
        SetupMode(
            value="socket",
            label="Socket Mode bot (two-way gateway chat)",
            fields=(BOT_TOKEN_FIELD, APP_TOKEN_FIELD),
        ),
        SetupMode(
            value="both",
            label="Both webhook and Socket Mode",
            fields=(WEBHOOK_URL_FIELD, BOT_TOKEN_FIELD, APP_TOKEN_FIELD),
        ),
    ),
    validate=_require_webhook_or_socket_tokens,
    verify=verify_slack,
)

__all__ = [
    "APP_TOKEN_FIELD",
    "BOT_TOKEN_FIELD",
    "SLACK_SETUP",
    "WEBHOOK_URL_FIELD",
]
