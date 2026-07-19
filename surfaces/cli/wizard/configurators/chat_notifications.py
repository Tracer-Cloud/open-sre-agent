"""Configurator handlers for chat-bot notification channels."""

from __future__ import annotations

from integrations.store import upsert_integration
from platform.terminal.theme import ERROR, GLYPH_ERROR, SECONDARY, WARNING
from surfaces.cli.wizard._ui import (
    Choice,
    _choose,
    _console,
    _integration_defaults,
    _prompt_value,
    _render_integration_result,
    _string_value,
)
from surfaces.cli.wizard.env_sync import sync_env_secret, sync_env_values
from surfaces.cli.wizard.integration_health import (
    validate_discord_bot,
    validate_rocketchat,
    validate_rocketchat_webhook,
    validate_slack_webhook,
    validate_telegram_bot,
)


def _configure_slack() -> tuple[str, str]:
    _, credentials = _integration_defaults("slack")
    mode = _choose(
        "Slack setup:",
        [
            Choice(value="webhook", label="Incoming webhook (outbound delivery)"),
            Choice(value="socket", label="Socket Mode bot (two-way gateway chat)"),
            Choice(value="both", label="Both webhook and Socket Mode"),
        ],
        default="webhook",
    )
    creds = dict(credentials)

    if mode in {"webhook", "both"}:
        while True:
            webhook_url = _prompt_value(
                "Slack webhook URL",
                default=_string_value(creds.get("webhook_url")),
                secret=True,
            )
            with _console.status("Validating Slack webhook...", spinner="dots"):
                result = validate_slack_webhook(webhook_url=webhook_url)
            _render_integration_result("Slack webhook", result)
            if result.ok:
                creds["webhook_url"] = webhook_url
                break
            _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")

    if mode in {"socket", "both"}:
        bot_token = _prompt_value(
            "Slack bot token (xoxb-…)",
            default=_string_value(creds.get("bot_token")),
            secret=True,
        )
        app_token = _prompt_value(
            "Slack app-level token (xapp-…)",
            default=_string_value(creds.get("app_token")),
            secret=True,
        )
        if not bot_token.startswith("xoxb-") or not app_token.startswith("xapp-"):
            _console.print(
                f"[{ERROR}]{GLYPH_ERROR} Socket Mode needs xoxb- bot token and xapp- app token.[/]"
            )
            raise SystemExit(1)
        creds["bot_token"] = bot_token
        creds["app_token"] = app_token
        sync_env_secret("SLACK_BOT_TOKEN", bot_token)
        sync_env_secret("SLACK_APP_TOKEN", app_token)

    upsert_integration("slack", {"credentials": creds})
    env_path = sync_env_values({})
    return "Slack", str(env_path)


def _configure_discord() -> tuple[str, str]:
    _, credentials = _integration_defaults("discord")
    _console.print(
        "\n[bold]Discord Integration[/bold]\n"
        f"[{SECONDARY}]Get your credentials from https://discord.com/developers/applications.[/]\n"
    )
    while True:
        bot_token = _prompt_value(
            "Discord bot token",
            default=_string_value(credentials.get("bot_token")),
            secret=True,
        )
        application_id = _prompt_value(
            "Discord application ID",
            default=_string_value(credentials.get("application_id")),
        )
        public_key = _prompt_value(
            "Discord public key (from Developer Portal)",
            default=_string_value(credentials.get("public_key")),
        )
        default_channel_id = _prompt_value(
            "Default channel ID (optional)",
            default=_string_value(credentials.get("default_channel_id")),
            allow_empty=True,
        )
        with _console.status("Validating Discord bot token...", spinner="dots"):
            result = validate_discord_bot(bot_token=bot_token)
        _render_integration_result("Discord", result)
        if result.ok:
            upsert_integration(
                "discord",
                {
                    "credentials": {
                        "bot_token": bot_token,
                        "application_id": application_id,
                        "public_key": public_key,
                        "default_channel_id": default_channel_id,
                    }
                },
            )
            from integrations.cli import _register_discord_slash_command

            _register_discord_slash_command(application_id, bot_token)
            sync_env_secret("DISCORD_BOT_TOKEN", bot_token)
            env_path = sync_env_values(
                {
                    "DISCORD_APPLICATION_ID": application_id,
                    "DISCORD_PUBLIC_KEY": public_key,
                    "DISCORD_DEFAULT_CHANNEL_ID": default_channel_id,
                }
            )
            return "Discord", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_rocketchat() -> tuple[str, str]:
    _, credentials = _integration_defaults("rocketchat")
    _console.print(
        "\n[bold]Rocket.Chat Integration[/bold]\n"
        f"[{SECONDARY}]Personal Access Token: create one under My Account > Personal Access "
        "Tokens (the token page also shows your user ID). Incoming webhook: create one "
        "under Administration > Integrations > Incoming.[/]\n"
    )
    mode = _choose(
        "Rocket.Chat setup:",
        [
            Choice(value="token", label="Personal access token (dynamic channel targeting)"),
            Choice(value="webhook", label="Incoming webhook (fixed destination)"),
            Choice(value="both", label="Both token and webhook"),
        ],
        default="token",
    )
    creds = dict(credentials)
    env_values: dict[str, str] = {}

    if mode == "token" and creds.get("webhook_url"):
        # Delivery prefers the webhook when both are set, so a stale webhook
        # from a prior run would otherwise silently keep overriding this
        # explicit token-only choice.
        creds["webhook_url"] = None

    if mode in {"token", "both"}:
        while True:
            server_url = _prompt_value(
                "Rocket.Chat server URL (e.g. https://open.rocket.chat)",
                default=_string_value(creds.get("server_url")),
            )
            auth_token = _prompt_value(
                "Rocket.Chat personal access token",
                default=_string_value(creds.get("auth_token")),
                secret=True,
            )
            user_id = _prompt_value(
                "Rocket.Chat user ID",
                default=_string_value(creds.get("user_id")),
            )
            default_channel = _prompt_value(
                "Default channel (e.g. #incidents, recommended for delivery)",
                default=_string_value(creds.get("default_channel")),
                allow_empty=True,
            )
            with _console.status("Validating Rocket.Chat credentials...", spinner="dots"):
                result = validate_rocketchat(
                    server_url=server_url, auth_token=auth_token, user_id=user_id
                )
            _render_integration_result("Rocket.Chat", result)
            if result.ok:
                creds["server_url"] = server_url
                creds["auth_token"] = auth_token
                creds["user_id"] = user_id
                creds["default_channel"] = default_channel or None
                sync_env_secret("ROCKETCHAT_AUTH_TOKEN", auth_token)
                env_values["ROCKETCHAT_SERVER_URL"] = server_url
                env_values["ROCKETCHAT_USER_ID"] = user_id
                if default_channel:
                    env_values["ROCKETCHAT_DEFAULT_CHANNEL"] = default_channel
                else:
                    _console.print(
                        f"[{WARNING}]No default channel set — token-based report deliveries "
                        "need ROCKETCHAT_DEFAULT_CHANNEL to send messages.[/]"
                    )
                break
            _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")

    if mode in {"webhook", "both"}:
        while True:
            webhook_url = _prompt_value(
                "Rocket.Chat incoming webhook URL",
                default=_string_value(creds.get("webhook_url")),
                secret=True,
            )
            with _console.status("Validating Rocket.Chat webhook...", spinner="dots"):
                result = validate_rocketchat_webhook(webhook_url=webhook_url)
            _render_integration_result("Rocket.Chat webhook", result)
            if result.ok:
                # The URL embeds its token, so it stays in the integration
                # store only — like SLACK_WEBHOOK_URL, it is not synced to
                # .env (sync_env_secret rejects keys not classified as
                # sensitive, and *_URL is not).
                creds["webhook_url"] = webhook_url
                break
            _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")

    upsert_integration("rocketchat", {"credentials": creds})
    env_path = sync_env_values(env_values)
    return "Rocket.Chat", str(env_path)


def _configure_telegram() -> tuple[str, str]:
    _, credentials = _integration_defaults("telegram")
    _console.print(
        "\n[bold]Telegram Integration[/bold]\n"
        f"[{SECONDARY}]Create a bot with @BotFather, add it to your chat, then find "
        "chat_id via getUpdates. See docs/messaging/telegram for details.[/]\n"
    )
    while True:
        bot_token = _prompt_value(
            "Telegram bot token",
            default=_string_value(credentials.get("bot_token")),
            secret=True,
        )
        default_chat_id = _prompt_value(
            "Default chat ID (recommended for delivery)",
            default=_string_value(credentials.get("default_chat_id")),
            allow_empty=True,
        )
        with _console.status("Validating Telegram bot token...", spinner="dots"):
            result = validate_telegram_bot(bot_token=bot_token)
        _render_integration_result("Telegram", result)
        if result.ok:
            upsert_integration(
                "telegram",
                {
                    "credentials": {
                        "bot_token": bot_token,
                        "default_chat_id": default_chat_id or None,
                    }
                },
            )
            sync_env_secret("TELEGRAM_BOT_TOKEN", bot_token)
            env_values: dict[str, str] = {}
            if default_chat_id:
                env_values["TELEGRAM_DEFAULT_CHAT_ID"] = default_chat_id
            env_path = sync_env_values(env_values)
            if not default_chat_id:
                _console.print(
                    f"[{WARNING}]No default chat ID set — Hermes, watchdog, and scheduled "
                    "deliveries need TELEGRAM_DEFAULT_CHAT_ID to send messages.[/]"
                )
            return "Telegram", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
