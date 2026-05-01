"""Canonical strict models for normalized integration configuration."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from app.config import get_tracer_base_url
from app.integrations._validators import (
    normalize_bearer,
    normalize_bool_str,
    normalize_str,
    normalize_url,
    normalize_with_default,
)
from app.strict_config import StrictConfigModel
from app.utils.url_validation import validate_https_or_loopback_http_url

_LOCAL_GRAFANA_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}
DEFAULT_HONEYCOMB_BASE_URL = "https://api.honeycomb.io"
DEFAULT_HONEYCOMB_DATASET = "__all__"
DEFAULT_CORALOGIX_BASE_URL = "https://api.coralogix.com"
DEFAULT_OPSGENIE_BASE_URLS: dict[str, str] = {
    "us": "https://api.opsgenie.com",
    "eu": "https://api.eu.opsgenie.com",
}


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class GrafanaIntegrationConfig(StrictConfigModel):
    """Normalized Grafana credentials used by resolution and verification flows."""

    endpoint: str
    api_key: str = ""
    integration_id: str = ""

    _normalize_endpoint = field_validator("endpoint", mode="before")(normalize_url())

    @property
    def is_local(self) -> bool:
        host = urlparse(self.endpoint).hostname or ""
        return host in _LOCAL_GRAFANA_HOSTS


class DatadogIntegrationConfig(StrictConfigModel):
    """Normalized Datadog credentials used by resolution and verification flows."""

    api_key: str
    app_key: str
    site: str = "datadoghq.com"
    integration_id: str = ""

    _normalize_site = field_validator("site", mode="before")(
        normalize_with_default("datadoghq.com")
    )

    @property
    def base_url(self) -> str:
        return f"https://api.{self.site}"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "DD-API-KEY": self.api_key,
            "DD-APPLICATION-KEY": self.app_key,
            "Content-Type": "application/json",
        }


class HoneycombIntegrationConfig(StrictConfigModel):
    """Normalized Honeycomb credentials used by resolution and verification flows."""

    api_key: str
    dataset: str = DEFAULT_HONEYCOMB_DATASET
    base_url: str = DEFAULT_HONEYCOMB_BASE_URL
    integration_id: str = ""

    _normalize_dataset = field_validator("dataset", mode="before")(
        normalize_with_default(DEFAULT_HONEYCOMB_DATASET)
    )
    _normalize_base_url = field_validator("base_url", mode="before")(
        normalize_url(DEFAULT_HONEYCOMB_BASE_URL)
    )


class CoralogixIntegrationConfig(StrictConfigModel):
    """Normalized Coralogix credentials used by resolution and verification flows."""

    api_key: str
    base_url: str = DEFAULT_CORALOGIX_BASE_URL
    application_name: str = ""
    subsystem_name: str = ""
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(
        normalize_url(DEFAULT_CORALOGIX_BASE_URL)
    )


# ---------------------------------------------------------------------------
# Cloud / Infrastructure
# ---------------------------------------------------------------------------


class AWSStaticCredentials(StrictConfigModel):
    """Static AWS access key credentials."""

    access_key_id: str
    secret_access_key: str
    session_token: str = ""


class AWSIntegrationConfig(StrictConfigModel):
    """Normalized AWS integration config supporting role or static keys."""

    region: str = "us-east-1"
    role_arn: str = ""
    external_id: str = ""
    credentials: AWSStaticCredentials | None = None
    integration_id: str = ""

    _normalize_region = field_validator("region", mode="before")(
        normalize_with_default("us-east-1")
    )

    @model_validator(mode="after")
    def _require_auth_method(self) -> AWSIntegrationConfig:
        if self.role_arn or self.credentials:
            return self
        raise ValueError(
            "AWS integration requires either role_arn or credentials.access_key_id/secret_access_key."
        )


class VercelIntegrationConfig(StrictConfigModel):
    """Normalized Vercel credentials used by resolution and verification flows."""

    api_token: str
    team_id: str = ""
    integration_id: str = ""

    _normalize_api_token = field_validator("api_token", mode="before")(normalize_str())
    _normalize_team_id = field_validator("team_id", mode="before")(normalize_str())

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    @property
    def team_params(self) -> dict[str, str]:
        return {"teamId": self.team_id} if self.team_id else {}


# ---------------------------------------------------------------------------
# Alerting & Incident Management
# ---------------------------------------------------------------------------


class SlackWebhookConfig(StrictConfigModel):
    """Slack webhook runtime config."""

    webhook_url: str

    @model_validator(mode="after")
    def _require_https_slack_url(self) -> SlackWebhookConfig:
        parsed = urlparse(self.webhook_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Slack webhook must be a valid HTTPS URL.")
        if "slack.com" not in parsed.netloc:
            raise ValueError("Slack webhook host must be a Slack domain.")
        return self


class OpsGenieIntegrationConfig(StrictConfigModel):
    """Normalized OpsGenie credentials used by resolution and verification flows."""

    api_key: str
    region: str = "us"
    integration_id: str = ""

    @field_validator("region", mode="before")
    @classmethod
    def _normalize_region(cls, value: object) -> str:
        raw = str(value or "us").strip().lower()
        return raw if raw in DEFAULT_OPSGENIE_BASE_URLS else "us"

    @property
    def base_url(self) -> str:
        return DEFAULT_OPSGENIE_BASE_URLS.get(self.region, DEFAULT_OPSGENIE_BASE_URLS["us"])

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"GenieKey {self.api_key}",
            "Content-Type": "application/json",
        }


class AlertmanagerIntegrationConfig(StrictConfigModel):
    """Normalized Alertmanager credentials used by resolution and verification flows."""

    base_url: str
    bearer_token: str = ""
    username: str = ""
    password: str = ""
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(normalize_url())
    _normalize_strs = field_validator("bearer_token", "username", "password", mode="before")(
        normalize_str()
    )

    @model_validator(mode="after")
    def _no_dual_auth(self) -> AlertmanagerIntegrationConfig:
        if self.bearer_token and self.username:
            raise ValueError(
                "Alertmanager config has both bearer_token and username set; "
                "use one auth method only."
            )
        return self

    @property
    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    @property
    def basic_auth(self) -> tuple[str, str] | None:
        if self.username and self.password:
            return (self.username, self.password)
        return None


class SplunkIntegrationConfig(StrictConfigModel):
    """Normalized Splunk credentials used by resolution and verification flows."""

    base_url: str
    token: str = ""
    index: str = "main"
    verify_ssl: bool = True
    ca_bundle: str = ""
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(normalize_url())
    _normalize_token = field_validator("token", mode="before")(normalize_str())
    _normalize_index = field_validator("index", mode="before")(normalize_with_default("main"))
    _normalize_ca_bundle = field_validator("ca_bundle", mode="before")(normalize_str())

    @property
    def ssl_verify(self) -> bool | str:
        if self.ca_bundle:
            return self.ca_bundle
        return self.verify_ssl

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.token)


# ---------------------------------------------------------------------------
# Source Control & CI/CD
# ---------------------------------------------------------------------------


class ArgoCDIntegrationConfig(StrictConfigModel):
    """Normalized Argo CD credentials used by resolution and verification flows."""

    base_url: str
    bearer_token: str = ""
    username: str = ""
    password: str = ""
    project: str = ""
    app_namespace: str = ""
    verify_ssl: bool = True
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        normalized = str(value or "").strip().rstrip("/")
        return validate_https_or_loopback_http_url(normalized, service_name="Argo CD")

    _normalize_bearer_token = field_validator("bearer_token", mode="before")(normalize_bearer())
    _normalize_strs = field_validator(
        "username", "password", "project", "app_namespace", "integration_id", mode="before"
    )(normalize_str())
    _normalize_verify_ssl = field_validator("verify_ssl", mode="before")(normalize_bool_str())

    @model_validator(mode="after")
    def _no_dual_auth(self) -> ArgoCDIntegrationConfig:
        if self.bearer_token and (self.username or self.password):
            raise ValueError(
                "Argo CD config has both bearer_token and username/password set; "
                "use one auth method only."
            )
        return self

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and (self.bearer_token or (self.username and self.password)))


class GitLabIntegrationConfig(StrictConfigModel):
    """Normalized GitLab credentials used by resolution and verification flows."""

    url: str
    access_token: str
    integration_id: str = ""


# ---------------------------------------------------------------------------
# Error Tracking & APM
# ---------------------------------------------------------------------------


class SentryIntegrationConfig(StrictConfigModel):
    """Normalized Sentry credentials — kept for type-check consumers."""

    base_url: str = "https://sentry.io"
    organization_slug: str = ""
    auth_token: str = ""
    project_slug: str = ""
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(
        normalize_url("https://sentry.io")
    )
    _normalize_strs = field_validator(
        "organization_slug", "auth_token", "project_slug", mode="before"
    )(normalize_str())


# ---------------------------------------------------------------------------
# Databases — Relational
# ---------------------------------------------------------------------------


class PostgreSQLIntegrationConfig(StrictConfigModel):
    """Normalized PostgreSQL credentials used by resolution and verification flows."""

    host: str
    port: int = 5432
    database: str
    username: str = "postgres"
    password: str = ""
    ssl_mode: str = "prefer"
    integration_id: str = ""

    _normalize_host = field_validator("host", mode="before")(normalize_str())
    _normalize_database = field_validator("database", mode="before")(normalize_str())
    _normalize_username = field_validator("username", mode="before")(
        normalize_with_default("postgres")
    )
    _normalize_ssl_mode = field_validator("ssl_mode", mode="before")(
        normalize_with_default("prefer")
    )


class MySQLIntegrationConfig(StrictConfigModel):
    """Normalized MySQL credentials used by resolution and verification flows."""

    host: str
    port: int = 3306
    database: str
    username: str = "root"
    password: str = ""
    ssl_mode: str = "preferred"
    integration_id: str = ""

    _normalize_host = field_validator("host", mode="before")(normalize_str())
    _normalize_database = field_validator("database", mode="before")(normalize_str())
    _normalize_username = field_validator("username", mode="before")(normalize_with_default("root"))
    _normalize_ssl_mode = field_validator("ssl_mode", mode="before")(
        normalize_with_default("preferred")
    )


class MariaDBIntegrationConfig(StrictConfigModel):
    """Normalized MariaDB credentials used by resolution and verification flows."""

    host: str
    port: int = 3306
    database: str
    username: str
    password: str = ""
    ssl: bool = True
    integration_id: str = ""

    _normalize_strs = field_validator("host", "database", "username", mode="before")(
        normalize_str()
    )


class AzureSQLIntegrationConfig(StrictConfigModel):
    """Normalized Azure SQL Database credentials used by resolution and verification flows."""

    server: str
    port: int = 1433
    database: str
    username: str = ""
    password: str = ""
    driver: str = "ODBC Driver 18 for SQL Server"
    encrypt: bool = True
    integration_id: str = ""

    _normalize_strs = field_validator("server", "database", "username", mode="before")(
        normalize_str()
    )
    _normalize_driver = field_validator("driver", mode="before")(
        normalize_with_default("ODBC Driver 18 for SQL Server")
    )


# ---------------------------------------------------------------------------
# Databases — Document / NoSQL
# ---------------------------------------------------------------------------


class MongoDBIntegrationConfig(StrictConfigModel):
    """Normalized MongoDB credentials used by resolution and verification flows."""

    connection_string: str
    database: str = ""
    auth_source: str = "admin"
    tls: bool = True
    integration_id: str = ""

    _normalize_connection_string = field_validator("connection_string", mode="before")(
        normalize_str()
    )
    _normalize_auth_source = field_validator("auth_source", mode="before")(
        normalize_with_default("admin")
    )


class MongoDBAtlasIntegrationConfig(StrictConfigModel):
    """Normalized MongoDB Atlas API credentials used by resolution and verification flows."""

    api_public_key: str
    api_private_key: str
    project_id: str
    base_url: str = "https://cloud.mongodb.com/api/atlas/v2"
    integration_id: str = ""

    _normalize_strs = field_validator(
        "api_public_key", "api_private_key", "project_id", mode="before"
    )(normalize_str())
    _normalize_base_url = field_validator("base_url", mode="before")(
        normalize_url("https://cloud.mongodb.com/api/atlas/v2")
    )


# ---------------------------------------------------------------------------
# Message Queues
# ---------------------------------------------------------------------------


class RabbitMQIntegrationConfig(StrictConfigModel):
    """Normalized RabbitMQ Management API credentials used by resolution and verification flows."""

    host: str
    management_port: int = 15672
    username: str
    password: str = ""
    vhost: str = "/"
    ssl: bool = False
    verify_ssl: bool = True
    integration_id: str = ""

    _normalize_strs = field_validator("host", "username", mode="before")(normalize_str())
    _normalize_vhost = field_validator("vhost", mode="before")(normalize_with_default("/"))


# ---------------------------------------------------------------------------
# Logging & Telemetry
# ---------------------------------------------------------------------------


class BetterStackIntegrationConfig(StrictConfigModel):
    """Normalized Better Stack Telemetry SQL Query API credentials."""

    query_endpoint: str
    username: str
    password: str = ""
    sources: list[str] = []
    integration_id: str = ""

    _normalize_endpoint = field_validator("query_endpoint", mode="before")(normalize_url())
    _normalize_username = field_validator("username", mode="before")(normalize_str())

    @field_validator("sources", mode="before")
    @classmethod
    def _normalize_sources(cls, value: object) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []


# ---------------------------------------------------------------------------
# Productivity & Collaboration
# ---------------------------------------------------------------------------


class JiraIntegrationConfig(StrictConfigModel):
    """Normalized Jira credentials used by resolution and verification flows."""

    base_url: str
    email: str
    api_token: str
    project_key: str
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(normalize_url())
    _normalize_strs = field_validator("email", "api_token", "project_key", mode="before")(
        normalize_str()
    )

    @property
    def auth(self) -> tuple[str, str]:
        return (self.email, self.api_token)

    @property
    def api_base(self) -> str:
        return f"{self.base_url}/rest/api/3"


class NotionIntegrationConfig(StrictConfigModel):
    """Normalized Notion credentials used by resolution and verification flows."""

    api_key: str
    database_id: str
    integration_id: str = ""

    _normalize_strs = field_validator("api_key", "database_id", mode="before")(normalize_str())


class TrelloIntegrationConfig(StrictConfigModel):
    """Normalized Trello credentials."""

    api_key: str
    api_token: str
    integration_id: str = ""

    _normalize_strs = field_validator("api_key", "api_token", mode="before")(normalize_str())


class GoogleDocsIntegrationConfig(StrictConfigModel):
    """Normalized Google Docs (Drive API) credentials for incident report generation."""

    credentials_file: str
    folder_id: str
    integration_id: str = ""
    timeout_seconds: int = 30

    _normalize_credentials_file = field_validator("credentials_file", mode="before")(
        normalize_str()
    )

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _validate_timeout(cls, value: object) -> int:
        if isinstance(value, str):
            try:
                timeout = int(value)
            except ValueError:
                return 30
        elif isinstance(value, int | float):
            timeout = int(value)
        else:
            return 30
        return max(5, min(timeout, 300))


# ---------------------------------------------------------------------------
# Messaging Bots
# ---------------------------------------------------------------------------


class DiscordBotConfig(StrictConfigModel):
    """Discord runtime config."""

    bot_token: str
    application_id: str = ""
    public_key: str = ""
    default_channel_id: str | None = None

    @field_validator("bot_token", mode="before")
    @classmethod
    def _validate_bot_token(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("bot_token cannot be empty or just whitespace")
        return stripped

    @field_validator("public_key", mode="before")
    @classmethod
    def _validate_public_key(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if stripped and not re.fullmatch(r"[0-9a-fA-F]+", stripped):
            raise ValueError("public_key must be a valid hexadecimal string")
        return stripped


class TelegramBotConfig(StrictConfigModel):
    """Telegram Bot runtime config."""

    bot_token: str
    default_chat_id: str | None = None

    @field_validator("bot_token", mode="before")
    @classmethod
    def _validate_bot_token(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("bot_token cannot be empty or just whitespace")
        return stripped


# ---------------------------------------------------------------------------
# Tracer internal
# ---------------------------------------------------------------------------


class TracerIntegrationConfig(StrictConfigModel):
    """Tracer API access config."""

    base_url: str = Field(default_factory=get_tracer_base_url)
    jwt_token: str

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        return str(value or get_tracer_base_url()).strip() or get_tracer_base_url()

    _normalize_token = field_validator("jwt_token", mode="before")(normalize_bearer())


# ---------------------------------------------------------------------------
# SaaS / workflow integrations (config-only, no active verifier)
# ---------------------------------------------------------------------------


class PrefectIntegrationConfig(StrictConfigModel):
    api_url: str = "https://api.prefect.cloud/api"
    api_key: str = ""
    account_id: str = ""
    workspace_id: str = ""
    integration_id: str = ""

    _normalize_api_url = field_validator("api_url", mode="before")(
        normalize_url("https://api.prefect.cloud/api")
    )
    _normalize_strs = field_validator("api_key", "account_id", "workspace_id", mode="before")(
        normalize_str()
    )
