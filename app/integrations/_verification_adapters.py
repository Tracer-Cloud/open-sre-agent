"""Shared verification adapters and service-specific verifiers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import boto3
import httpx
import requests

from app.auth.jwt_auth import extract_org_id_from_jwt
from app.config import get_tracer_base_url
from app.integrations.azure_sql import build_azure_sql_config, validate_azure_sql_config
from app.integrations.betterstack import build_betterstack_config, validate_betterstack_config
from app.integrations.config_models import (
    AWSIntegrationConfig,
    CoralogixIntegrationConfig,
    GoogleDocsIntegrationConfig,
    GrafanaIntegrationConfig,
    HoneycombIntegrationConfig,
    SlackWebhookConfig,
    TracerIntegrationConfig,
)
from app.integrations.github_mcp import build_github_mcp_config, validate_github_mcp_config
from app.integrations.mariadb import build_mariadb_config, validate_mariadb_config
from app.integrations.mongodb import build_mongodb_config, validate_mongodb_config
from app.integrations.mongodb_atlas import build_mongodb_atlas_config, validate_mongodb_atlas_config
from app.integrations.mysql import build_mysql_config, validate_mysql_config
from app.integrations.openclaw import build_openclaw_config, validate_openclaw_config
from app.integrations.postgresql import build_postgresql_config, validate_postgresql_config
from app.integrations.rabbitmq import build_rabbitmq_config, validate_rabbitmq_config
from app.integrations.sentry import build_sentry_config, validate_sentry_config
from app.services.alertmanager import AlertmanagerClient, AlertmanagerConfig
from app.services.argocd import ArgoCDClient, ArgoCDConfig
from app.services.coralogix import CoralogixClient
from app.services.datadog.client import DatadogClient, DatadogConfig
from app.services.google_docs import GoogleDocsClient
from app.services.honeycomb import HoneycombClient
from app.services.opsgenie import OpsGenieClient, OpsGenieConfig
from app.services.splunk import SplunkClient, SplunkConfig
from app.services.tracer_client.client import TracerClient
from app.services.vercel.client import VercelClient, VercelConfig
from app.services.victoria_logs import VictoriaLogsClient, VictoriaLogsConfig

VerifierFn = Callable[[str, dict[str, Any]], dict[str, str]]

_SUPPORTED_GRAFANA_TYPES = ("loki", "tempo", "prometheus")


def result(
    service: str,
    source: str,
    status: str,
    detail: str,
) -> dict[str, str]:
    return {
        "service": service,
        "source": source,
        "status": status,
        "detail": detail,
    }


def _verify_with_validation_result[ConfigT](
    service: str,
    source: str,
    config: dict[str, Any],
    *,
    build_config: Callable[[dict[str, Any]], ConfigT],
    validate_config: Callable[[ConfigT], Any],
) -> dict[str, str]:
    normalized_config = build_config(config)
    validation_result = validate_config(normalized_config)
    return result(
        service,
        source,
        "passed" if validation_result.ok else "failed",
        validation_result.detail,
    )


def build_validation_verifier[ConfigT](
    service: str,
    *,
    build_config: Callable[[dict[str, Any]], ConfigT],
    validate_config: Callable[[ConfigT], Any],
) -> VerifierFn:
    def _verifier(source: str, config: dict[str, Any]) -> dict[str, str]:
        return _verify_with_validation_result(
            service,
            source,
            config,
            build_config=build_config,
            validate_config=validate_config,
        )

    return _verifier


def build_probe_verifier[ConfigT](
    service: str,
    *,
    build_config: Callable[[dict[str, Any]], ConfigT],
    client_factory: Callable[[ConfigT], Any],
) -> VerifierFn:
    def _verifier(source: str, config: dict[str, Any]) -> dict[str, str]:
        try:
            normalized_config = build_config(config)
        except Exception as err:
            return result(service, source, "missing", str(err))
        probe_result = client_factory(normalized_config).probe_access()
        return result(service, source, probe_result.status, probe_result.detail)

    return _verifier


def _build_sts_client(config: dict[str, Any]) -> tuple[Any, str, str]:
    aws_config = AWSIntegrationConfig.model_validate(config)
    region = aws_config.region
    role_arn = aws_config.role_arn
    external_id = aws_config.external_id
    if role_arn:
        base_sts_client = boto3.client("sts", region_name=region)
        assume_role_args: dict[str, str] = {
            "RoleArn": role_arn,
            "RoleSessionName": "TracerIntegrationVerify",
        }
        if external_id:
            assume_role_args["ExternalId"] = external_id
        credentials = base_sts_client.assume_role(**assume_role_args)["Credentials"]
        return (
            boto3.client(
                "sts",
                region_name=region,
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
            ),
            region,
            "assume-role",
        )

    static_credentials = aws_config.credentials
    if static_credentials is None:
        raise ValueError("Missing AWS role_arn or credentials.")
    return (
        boto3.client(
            "sts",
            region_name=region,
            aws_access_key_id=static_credentials.access_key_id,
            aws_secret_access_key=static_credentials.secret_access_key,
            aws_session_token=static_credentials.session_token or None,
        ),
        region,
        "static-creds",
    )


def _verify_grafana(source: str, config: dict[str, Any]) -> dict[str, str]:
    grafana_config = GrafanaIntegrationConfig.model_validate(config)
    endpoint = grafana_config.endpoint
    api_key = grafana_config.api_key
    if not endpoint or not api_key:
        return result("grafana", source, "missing", "Missing endpoint or API token.")

    try:
        response = requests.get(
            f"{endpoint}/api/datasources",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return result("grafana", source, "failed", f"Datasource discovery failed: {exc}")

    datasources = payload if isinstance(payload, list) else []
    supported_types = sorted(
        {
            datasource_type
            for datasource in datasources
            for datasource_type in [str(datasource.get("type", "")).lower()]
            if any(keyword in datasource_type for keyword in _SUPPORTED_GRAFANA_TYPES)
        }
    )
    if not supported_types:
        return result(
            "grafana",
            source,
            "failed",
            "Connected, but no Loki, Tempo, or Prometheus datasources were discovered.",
        )

    return result(
        "grafana",
        source,
        "passed",
        f"Connected to {endpoint} and discovered {', '.join(supported_types)} datasources.",
    )


def _verify_aws(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        sts_client, region, mode = _build_sts_client(config)
        identity = sts_client.get_caller_identity()
    except Exception as exc:  # noqa: BLE001
        return result("aws", source, "failed", f"AWS STS check failed: {exc}")

    account = str(identity.get("Account", "")).strip()
    arn = str(identity.get("Arn", "")).strip()
    return result(
        "aws",
        source,
        "passed",
        (
            f"Connected to AWS STS via {mode} in {region}; "
            f"caller identity account={account or 'unknown'} arn={arn or 'unknown'}."
        ),
    )


def _verify_slack(
    source: str,
    config: dict[str, Any],
    *,
    send_slack_test: bool,
) -> dict[str, str]:
    try:
        slack_config = SlackWebhookConfig.model_validate(config)
    except Exception as err:
        return result("slack", source, "missing", str(err))

    webhook_url = slack_config.webhook_url
    if not webhook_url:
        return result("slack", source, "missing", "SLACK_WEBHOOK_URL is not configured.")

    if not send_slack_test:
        return result(
            "slack", source, "passed", "Configured. Use --send-slack-test to validate delivery."
        )

    payload = {
        "text": "Tracer integration test: Slack webhook is configured correctly.",
    }
    try:
        response = httpx.post(webhook_url, json=payload, timeout=10.0)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return result("slack", source, "failed", f"Webhook delivery failed: {exc}")
    return result("slack", source, "passed", "Webhook delivered test message successfully.")


def _verify_tracer(source: str, config: dict[str, Any]) -> dict[str, str]:
    tracer_config = TracerIntegrationConfig.model_validate(config)
    if not tracer_config.jwt_token:
        return result("tracer", source, "missing", "Missing JWT token.")

    base_url = tracer_config.base_url or get_tracer_base_url()
    try:
        org_id = extract_org_id_from_jwt(tracer_config.jwt_token)
    except Exception as err:
        return result("tracer", source, "failed", f"JWT decode failed: {err}")
    if not org_id:
        return result("tracer", source, "failed", "JWT did not contain an org identifier.")

    try:
        tracer_client = TracerClient(
            base_url=base_url,
            org_id=org_id,
            jwt_token=tracer_config.jwt_token,
        )
        integrations = tracer_client.get_all_integrations()
    except Exception as err:
        return result("tracer", source, "failed", f"Tracer API check failed: {err}")

    return result(
        "tracer",
        source,
        "passed",
        f"Connected to {base_url} for org {org_id} and listed {len(integrations)} integrations.",
    )


def _verify_discord(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        import discord  # type: ignore[import-not-found]
    except Exception:
        return result("discord", source, "failed", "discord.py is not installed.")

    bot_token = str(config.get("bot_token", "")).strip()
    if not bot_token:
        return result("discord", source, "missing", "Missing bot_token.")

    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)
    try:
        client.run(bot_token)
    except discord.LoginFailure as err:
        return result("discord", source, "failed", f"Discord login failed: {err}")
    except Exception as err:  # noqa: BLE001
        detail = str(err)
        if "run() cannot be called from a running event loop" in detail:
            return result("discord", source, "passed", "Discord bot token accepted.")
        return result("discord", source, "failed", f"Discord API check failed: {err}")
    return result("discord", source, "passed", "Discord bot token accepted.")


def _verify_telegram(source: str, config: dict[str, Any]) -> dict[str, str]:
    bot_token = str(config.get("bot_token", "")).strip()
    if not bot_token:
        return result("telegram", source, "missing", "Missing bot_token.")

    try:
        response = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return result("telegram", source, "failed", f"Telegram API check failed: {exc}")

    if not payload.get("ok"):
        return result(
            "telegram",
            source,
            "failed",
            f"Telegram API check failed: {payload.get('description', 'unknown error')}",
        )

    user = payload.get("result", {})
    username = str(user.get("username", "")).strip()
    return result(
        "telegram",
        source,
        "passed",
        f"Connected to Telegram bot @{username or 'unknown'}.",
    )


def _verify_snowflake(source: str, config: dict[str, Any]) -> dict[str, str]:
    account_identifier = str(config.get("account_identifier", "")).strip()
    token = str(config.get("token", "")).strip()
    if not account_identifier:
        return result("snowflake", source, "missing", "Missing account_identifier.")
    if not token:
        return result("snowflake", source, "missing", "Missing token credentials.")
    return result(
        "snowflake", source, "passed", f"Configured for Snowflake account {account_identifier}."
    )


def _verify_azure(source: str, config: dict[str, Any]) -> dict[str, str]:
    workspace_id = str(config.get("workspace_id", "")).strip()
    access_token = str(config.get("access_token", "")).strip()
    endpoint = str(config.get("endpoint", "https://api.loganalytics.io")).strip() or (
        "https://api.loganalytics.io"
    )
    if not workspace_id or not access_token:
        return result(
            "azure",
            source,
            "missing",
            "Missing workspace_id or access_token.",
        )
    return result(
        "azure",
        source,
        "passed",
        f"Configured for Azure Log Analytics workspace {workspace_id} via {endpoint}.",
    )


def _verify_openobserve(source: str, config: dict[str, Any]) -> dict[str, str]:
    base_url = str(config.get("base_url", "")).strip()
    api_token = str(config.get("api_token", "")).strip()
    username = str(config.get("username", "")).strip()
    password = str(config.get("password", "")).strip()
    if not base_url:
        return result("openobserve", source, "missing", "Missing base_url.")
    if not (api_token or (username and password)):
        return result("openobserve", source, "missing", "Missing API token or username/password.")
    return result(
        "openobserve", source, "passed", f"Configured for OpenObserve at {base_url.rstrip('/')}."
    )


def _verify_opensearch(source: str, config: dict[str, Any]) -> dict[str, str]:
    url = str(config.get("url", "")).strip()
    if not url:
        return result("opensearch", source, "missing", "Missing url.")
    return result(
        "opensearch", source, "passed", f"Configured for OpenSearch at {url.rstrip('/')}."
    )


_verify_github = build_validation_verifier(
    "github",
    build_config=build_github_mcp_config,
    validate_config=validate_github_mcp_config,
)
_verify_sentry = build_validation_verifier(
    "sentry",
    build_config=build_sentry_config,
    validate_config=validate_sentry_config,
)
_verify_mongodb = build_validation_verifier(
    "mongodb",
    build_config=build_mongodb_config,
    validate_config=validate_mongodb_config,
)
_verify_postgresql = build_validation_verifier(
    "postgresql",
    build_config=build_postgresql_config,
    validate_config=validate_postgresql_config,
)
_verify_azure_sql = build_validation_verifier(
    "azure_sql",
    build_config=build_azure_sql_config,
    validate_config=validate_azure_sql_config,
)
_verify_mongodb_atlas = build_validation_verifier(
    "mongodb_atlas",
    build_config=build_mongodb_atlas_config,
    validate_config=validate_mongodb_atlas_config,
)
_verify_mariadb = build_validation_verifier(
    "mariadb",
    build_config=build_mariadb_config,
    validate_config=validate_mariadb_config,
)
_verify_rabbitmq = build_validation_verifier(
    "rabbitmq",
    build_config=build_rabbitmq_config,
    validate_config=validate_rabbitmq_config,
)
_verify_betterstack = build_validation_verifier(
    "betterstack",
    build_config=build_betterstack_config,
    validate_config=validate_betterstack_config,
)
_verify_mysql = build_validation_verifier(
    "mysql",
    build_config=build_mysql_config,
    validate_config=validate_mysql_config,
)
_verify_openclaw = build_validation_verifier(
    "openclaw",
    build_config=build_openclaw_config,
    validate_config=validate_openclaw_config,
)


def _build_kafka_config(raw: dict[str, Any]) -> Any:
    from app.integrations.kafka import build_kafka_config

    return build_kafka_config(raw)


def _validate_kafka_config(config: Any) -> Any:
    from app.integrations.kafka import validate_kafka_config

    return validate_kafka_config(config)


def _build_clickhouse_config(raw: dict[str, Any]) -> Any:
    from app.integrations.clickhouse import build_clickhouse_config

    return build_clickhouse_config(raw)


def _validate_clickhouse_config(config: Any) -> Any:
    from app.integrations.clickhouse import validate_clickhouse_config

    return validate_clickhouse_config(config)


def _build_bitbucket_config(raw: dict[str, Any]) -> Any:
    from app.integrations.bitbucket import build_bitbucket_config

    return build_bitbucket_config(raw)


def _validate_bitbucket_config(config: Any) -> Any:
    from app.integrations.bitbucket import validate_bitbucket_config

    return validate_bitbucket_config(config)


_verify_kafka = build_validation_verifier(
    "kafka",
    build_config=_build_kafka_config,
    validate_config=_validate_kafka_config,
)
_verify_clickhouse = build_validation_verifier(
    "clickhouse",
    build_config=_build_clickhouse_config,
    validate_config=_validate_clickhouse_config,
)
_verify_bitbucket = build_validation_verifier(
    "bitbucket",
    build_config=_build_bitbucket_config,
    validate_config=_validate_bitbucket_config,
)

_verify_datadog = build_probe_verifier(
    "datadog",
    build_config=DatadogConfig.model_validate,
    client_factory=DatadogClient,
)
_verify_honeycomb = build_probe_verifier(
    "honeycomb",
    build_config=HoneycombIntegrationConfig.model_validate,
    client_factory=HoneycombClient,
)
_verify_coralogix = build_probe_verifier(
    "coralogix",
    build_config=CoralogixIntegrationConfig.model_validate,
    client_factory=CoralogixClient,
)
_verify_google_docs = build_probe_verifier(
    "google_docs",
    build_config=GoogleDocsIntegrationConfig.model_validate,
    client_factory=GoogleDocsClient,
)
_verify_vercel = build_probe_verifier(
    "vercel",
    build_config=VercelConfig.model_validate,
    client_factory=VercelClient,
)
_verify_opsgenie = build_probe_verifier(
    "opsgenie",
    build_config=OpsGenieConfig.model_validate,
    client_factory=OpsGenieClient,
)
_verify_alertmanager = build_probe_verifier(
    "alertmanager",
    build_config=AlertmanagerConfig.model_validate,
    client_factory=AlertmanagerClient,
)
_verify_argocd = build_probe_verifier(
    "argocd",
    build_config=ArgoCDConfig.model_validate,
    client_factory=ArgoCDClient,
)
_verify_splunk = build_probe_verifier(
    "splunk",
    build_config=SplunkConfig.model_validate,
    client_factory=SplunkClient,
)
_verify_victoria_logs = build_probe_verifier(
    "victoria_logs",
    build_config=VictoriaLogsConfig.model_validate,
    client_factory=VictoriaLogsClient,
)


def _verify_slack_without_test(source: str, config: dict[str, Any]) -> dict[str, str]:
    return _verify_slack(source, config, send_slack_test=False)


__all__ = [
    "VerifierFn",
    "_verify_alertmanager",
    "_verify_argocd",
    "_verify_aws",
    "_verify_azure",
    "_verify_azure_sql",
    "_verify_betterstack",
    "_verify_bitbucket",
    "_verify_clickhouse",
    "_verify_coralogix",
    "_verify_datadog",
    "_verify_discord",
    "_verify_github",
    "_verify_google_docs",
    "_verify_grafana",
    "_verify_honeycomb",
    "_verify_kafka",
    "_verify_mariadb",
    "_verify_mongodb",
    "_verify_mongodb_atlas",
    "_verify_mysql",
    "_verify_openclaw",
    "_verify_openobserve",
    "_verify_opensearch",
    "_verify_opsgenie",
    "_verify_postgresql",
    "_verify_rabbitmq",
    "_verify_sentry",
    "_verify_slack",
    "_verify_slack_without_test",
    "_verify_snowflake",
    "_verify_splunk",
    "_verify_telegram",
    "_verify_tracer",
    "_verify_vercel",
    "_verify_victoria_logs",
    "build_probe_verifier",
    "build_validation_verifier",
    "result",
]
