"""Slack gateway configuration loaded from env and integration store."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from config.strict_config import StrictConfigModel
from gateway.runtime.errors import GatewayConfigurationError
from integrations.messaging_security import MessagingIdentityPolicy, MessagingPlatform
from integrations.store import get_integration

logger = logging.getLogger(__name__)


class SlackGatewaySettings(StrictConfigModel):
    """Runtime settings for the Slack Socket Mode gateway."""

    bot_token: str
    app_token: str
    allowed_user_ids: list[str] = Field(default_factory=list)
    allow_open_workspace: bool = False
    max_concurrent_turns: int = Field(default=4, ge=1)
    status_update_interval_seconds: float = Field(default=1.5, gt=0)
    turn_timeout_seconds: float = Field(default=240.0, gt=0)


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
    gateway_turn_timeout_seconds: float = Field(default=240.0, gt=0)

    @field_validator("allowed_users", mode="before")
    @classmethod
    def parse_allowed_users(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


def load_slack_credentials() -> Mapping[str, Any]:
    """Load Slack credentials from the integration store."""

    try:
        record = get_integration(MessagingPlatform.SLACK.value)
    except Exception as exc:
        raise GatewayConfigurationError("Could not load Slack integration") from exc

    if not isinstance(record, Mapping):
        logger.info("Slack integration not configured; using env only")
        return {}

    credentials = record.get("credentials")
    if not isinstance(credentials, Mapping):
        logger.info("Slack integration has no credentials; using env only")
        return {}

    return credentials


def store_bot_token(credentials: Mapping[str, Any]) -> str:
    return str(credentials.get("bot_token") or "").strip()


def store_app_token(credentials: Mapping[str, Any]) -> str:
    return str(credentials.get("app_token") or "").strip()


def store_allowed_users(credentials: Mapping[str, Any]) -> list[str]:
    raw_policy = credentials.get("identity_policy")

    if not raw_policy:
        return []

    if not isinstance(raw_policy, Mapping):
        raise GatewayConfigurationError("Slack identity_policy must be an object")

    try:
        policy = MessagingIdentityPolicy.model_validate(raw_policy)
    except ValidationError as exc:
        raise GatewayConfigurationError("Invalid Slack identity_policy") from exc

    return list(policy.allowed_user_ids)


def choose_bot_token(env: SlackGatewayEnv, credentials: Mapping[str, Any]) -> str:
    token = env.bot_token or store_bot_token(credentials)

    if not token:
        raise GatewayConfigurationError(
            "Slack bot token is missing. Set SLACK_BOT_TOKEN (xoxb-…) or configure "
            "the Slack integration."
        )

    return token


def choose_app_token(env: SlackGatewayEnv, credentials: Mapping[str, Any]) -> str:
    token = env.app_token or store_app_token(credentials)

    if not token:
        raise GatewayConfigurationError(
            "Slack app-level token is missing. Set SLACK_APP_TOKEN (xapp-…) or configure "
            "the Slack integration."
        )

    return token


def choose_authorized_users(env: SlackGatewayEnv, credentials: Mapping[str, Any]) -> list[str]:
    return store_allowed_users(credentials) or env.allowed_users


def load_slack_gateway_settings() -> SlackGatewaySettings:
    """Load complete Slack gateway settings from env and the integration store."""

    try:
        env = SlackGatewayEnv()
    except ValidationError as exc:
        raise GatewayConfigurationError("Invalid Slack gateway configuration") from exc

    credentials = load_slack_credentials()
    allowed_users = choose_authorized_users(env, credentials)

    if not allowed_users and not env.allow_open_workspace:
        raise GatewayConfigurationError(
            "Slack gateway needs allowed users: run `opensre messaging allow -p slack -u <id>`, "
            "set SLACK_ALLOWED_USERS (comma-separated user IDs), "
            "or set SLACK_ALLOW_OPEN_WORKSPACE=1 to allow any workspace member (dogfood only)."
        )

    if env.allow_open_workspace and not allowed_users:
        logger.warning("SLACK_ALLOW_OPEN_WORKSPACE=1: any workspace member can talk to the bot")

    return SlackGatewaySettings(
        bot_token=choose_bot_token(env, credentials),
        app_token=choose_app_token(env, credentials),
        allowed_user_ids=allowed_users,
        allow_open_workspace=env.allow_open_workspace,
        max_concurrent_turns=env.gateway_max_concurrent,
        status_update_interval_seconds=env.gateway_status_update_interval_seconds,
        turn_timeout_seconds=env.gateway_turn_timeout_seconds,
    )
