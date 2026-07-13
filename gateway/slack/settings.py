"""Slack gateway configuration loaded from env."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from config.strict_config import StrictConfigModel
from gateway.runtime.errors import GatewayConfigurationError

logger = logging.getLogger(__name__)


class SlackGatewaySettings(StrictConfigModel):
    """Runtime settings for the Slack Socket Mode gateway."""

    bot_token: str
    app_token: str
    allowed_user_ids: list[str] = Field(default_factory=list)
    allow_open_workspace: bool = False
    max_concurrent_turns: int = Field(default=4, ge=1)
    status_update_interval_seconds: float = Field(default=1.5, gt=0)


class SlackGatewayEnv(BaseSettings):
    """Environment-backed Slack gateway settings.

    Tokens must come from the environment / secret store — never commit them.
    """

    model_config = SettingsConfigDict(env_prefix="SLACK_", extra="ignore")

    bot_token: str = ""
    app_token: str = ""
    # NoDecode keeps pydantic-settings from JSON-decoding the env value so the
    # CSV validator below can parse "U123,U456" instead of raising a SettingsError.
    allowed_users: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Explicit escape hatch only — empty allowlist alone must not open the bot.
    allow_open_workspace: bool = False
    gateway_max_concurrent: int = Field(default=4, ge=1)
    gateway_status_update_interval_seconds: float = Field(default=1.5, gt=0)

    @field_validator("allowed_users", mode="before")
    @classmethod
    def parse_allowed_users(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


def load_slack_gateway_settings() -> SlackGatewaySettings:
    """Load complete Slack gateway settings from ``SLACK_*`` env variables."""

    try:
        env = SlackGatewayEnv()
    except ValidationError as exc:
        raise GatewayConfigurationError("Invalid Slack gateway configuration") from exc

    if not env.bot_token or not env.app_token:
        raise GatewayConfigurationError(
            "Slack gateway needs SLACK_BOT_TOKEN (xoxb-…) and SLACK_APP_TOKEN (xapp-…)."
        )

    if not env.allowed_users and not env.allow_open_workspace:
        raise GatewayConfigurationError(
            "Slack gateway needs SLACK_ALLOWED_USERS (comma-separated user IDs), "
            "or set SLACK_ALLOW_OPEN_WORKSPACE=1 to allow any workspace member (dogfood only)."
        )

    if env.allow_open_workspace and not env.allowed_users:
        logger.warning("SLACK_ALLOW_OPEN_WORKSPACE=1: any workspace member can talk to the bot")

    return SlackGatewaySettings(
        bot_token=env.bot_token,
        app_token=env.app_token,
        allowed_user_ids=env.allowed_users,
        allow_open_workspace=env.allow_open_workspace,
        max_concurrent_turns=env.gateway_max_concurrent,
        status_update_interval_seconds=env.gateway_status_update_interval_seconds,
    )
