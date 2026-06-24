"""Shared integration catalog for normalization and resolution."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any

from app.config import get_tracer_base_url
from app.integrations.airflow import airflow_config_from_env
from app.integrations.airflow import classify as _classify_airflow
from app.integrations.alertmanager import classify as _classify_alertmanager
from app.integrations.argocd import classify as _classify_argocd
from app.integrations.aws import classify as _classify_aws
from app.integrations.azure import classify as _classify_azure
from app.integrations.azure_sql import build_azure_sql_config
from app.integrations.azure_sql import classify as _classify_azure_sql
from app.integrations.betterstack import build_betterstack_config
from app.integrations.betterstack import classify as _classify_betterstack
from app.integrations.bitbucket import classify as _classify_bitbucket
from app.integrations.config_models import (
    AlertmanagerIntegrationConfig,
    ArgoCDIntegrationConfig,
    AWSIntegrationConfig,
    CoralogixIntegrationConfig,
    DatadogIntegrationConfig,
    DiscordBotConfig,
    GrafanaIntegrationConfig,
    GroundcoverIntegrationConfig,
    HelmIntegrationConfig,
    HoneycombIntegrationConfig,
    IncidentIoIntegrationConfig,
    JiraIntegrationConfig,
    OpsGenieIntegrationConfig,
    PagerDutyIntegrationConfig,
    SlackWebhookConfig,
    SMTPIntegrationConfig,
    SplunkIntegrationConfig,
    TelegramBotConfig,
    TwilioIntegrationConfig,
    VictoriaLogsIntegrationConfig,
    WhatsAppConfig,
)
from app.integrations.coralogix import classify as _classify_coralogix
from app.integrations.dagster import build_dagster_config
from app.integrations.dagster import classify as _classify_dagster
from app.integrations.datadog import classify as _classify_datadog
from app.integrations.discord import classify as _classify_discord
from app.integrations.effective_models import EffectiveIntegrations
from app.integrations.github_mcp import build_github_mcp_config
from app.integrations.github_mcp import classify as _classify_github
from app.integrations.gitlab import DEFAULT_GITLAB_BASE_URL, build_gitlab_config
from app.integrations.gitlab import classify as _classify_gitlab
from app.integrations.grafana import classify as _classify_grafana
from app.integrations.groundcover import classify as _classify_groundcover
from app.integrations.helm import classify as _classify_helm
from app.integrations.honeycomb import classify as _classify_honeycomb
from app.integrations.incident_io import classify as _classify_incident_io
from app.integrations.jenkins import classify as _classify_jenkins
from app.integrations.jenkins import jenkins_config_from_env
from app.integrations.jira import classify as _classify_jira
from app.integrations.mariadb import build_mariadb_config
from app.integrations.mariadb import classify as _classify_mariadb
from app.integrations.mongodb import build_mongodb_config
from app.integrations.mongodb import classify as _classify_mongodb
from app.integrations.mongodb_atlas import build_mongodb_atlas_config
from app.integrations.mongodb_atlas import classify as _classify_mongodb_atlas
from app.integrations.mysql import build_mysql_config
from app.integrations.mysql import classify as _classify_mysql
from app.integrations.openclaw import build_openclaw_config
from app.integrations.openclaw import classify as _classify_openclaw
from app.integrations.openobserve import classify as _classify_openobserve
from app.integrations.opensearch import classify as _classify_opensearch
from app.integrations.opsgenie import classify as _classify_opsgenie
from app.integrations.pagerduty import classify as _classify_pagerduty
from app.integrations.postgresql import build_postgresql_config
from app.integrations.postgresql import classify as _classify_postgresql
from app.integrations.posthog_mcp import DEFAULT_POSTHOG_MCP_URL, build_posthog_mcp_config
from app.integrations.posthog_mcp import classify as _classify_posthog_mcp
from app.integrations.rabbitmq import build_rabbitmq_config
from app.integrations.rabbitmq import classify as _classify_rabbitmq
from app.integrations.rds import classify as _classify_rds
from app.integrations.rds import rds_config_from_env
from app.integrations.redis import classify as _classify_redis
from app.integrations.redis import redis_config_from_env
from app.integrations.registry import (
    DIRECT_CLASSIFIED_EFFECTIVE_SERVICES,
    SKIP_CLASSIFIED_SERVICES,
    family_key,
    service_key,
)
from app.integrations.sentry import build_sentry_config
from app.integrations.sentry import classify as _classify_sentry
from app.integrations.sentry_mcp import DEFAULT_SENTRY_MCP_URL, build_sentry_mcp_config
from app.integrations.sentry_mcp import classify as _classify_sentry_mcp
from app.integrations.signoz import classify as _classify_signoz
from app.integrations.signoz import signoz_config_from_env
from app.integrations.smtp import classify as _classify_smtp
from app.integrations.snowflake import classify as _classify_snowflake
from app.integrations.splunk import classify as _classify_splunk
from app.integrations.store import _STRUCTURAL_RECORD_FIELDS, load_integrations
from app.integrations.supabase import build_supabase_config
from app.integrations.supabase import classify as _classify_supabase
from app.integrations.telegram import classify as _classify_telegram
from app.integrations.tempo import classify as _classify_tempo
from app.integrations.tempo import tempo_config_from_env
from app.integrations.temporal import classify as _classify_temporal
from app.integrations.twilio import classify as _classify_twilio
from app.integrations.vercel import classify as _classify_vercel
from app.integrations.victoria_logs import classify as _classify_victoria_logs
from app.integrations.whatsapp import classify as _classify_whatsapp
from app.llm_credentials import resolve_env_credential
from app.services.temporal import TemporalConfig
from app.services.vercel import VercelConfig
from app.utils.coercion import safe_int
from app.utils.errors import report_exception

logger = logging.getLogger(__name__)


def _report_env_loader_failure(exc: BaseException, *, integration: str) -> None:
    """Route a per-vendor env-loader failure to Sentry + warning log.

    Replaces ``except Exception: pass`` and ``logger.debug(..., exc_info=True)``
    paths in ``load_env_integrations``: integration is still skipped, but the
    misconfiguration reaches Sentry rather than being lost to debug output
    (#1468).
    """
    report_exception(
        exc,
        logger=logger,
        message=f"env_loader_failed: integration={integration}",
        severity="warning",
        tags={
            "surface": "integration",
            "component": "app.integrations._catalog_impl",
            "integration": integration,
            "event": "env_loader_failed",
        },
    )


def _should_publish_instance_siblings(instances: object) -> bool:
    """Return whether an effective integration should expose its ``instances`` list."""
    if not isinstance(instances, list) or not instances:
        return False
    if len(instances) > 1:
        return True
    return str(instances[0].get("name", "default")) != "default"


def _record_instances(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a record (v1 or v2 shape) into a list of instance dicts.

    v2 records return their ``instances`` list directly. v1 records are
    migrated on the fly: ``credentials`` plus every non-structural top-level
    field (e.g. AWS ``role_arn``) become the single ``default`` instance's
    credentials. This matches the v1→v2 store migration so downstream
    classification logic reads ONE uniform shape.
    """
    if isinstance(record.get("instances"), list):
        return [inst if isinstance(inst, dict) else {} for inst in record["instances"]]
    credentials = dict(record.get("credentials", {}))
    for key, value in record.items():
        if key in _STRUCTURAL_RECORD_FIELDS or key == "credentials":
            continue
        credentials.setdefault(key, value)
    return [{"name": "default", "tags": {}, "credentials": credentials}]


def classify_integrations(integrations: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify active integrations by service into normalized runtime configs.

    Backward compat: for each ``service``, ``resolved[service]`` is the flat
    config dict of the DEFAULT (first) instance, matching the pre-multi-instance
    contract. When multiple instances exist (or an instance has an explicit
    non-``default`` name), a sibling key ``_all_{service}_instances`` carries
    all of them as ``[{name, tags, config, integration_id}, ...]``. See
    ``app/integrations/selectors.py`` for consumers.
    """
    resolved: dict[str, Any] = {}
    all_instances: dict[str, list[dict[str, Any]]] = {}

    active = [integration for integration in integrations if integration.get("status") == "active"]

    for integration in active:
        service = str(integration.get("service") or "").strip()
        if not service:
            continue

        service_lower = service.lower()
        if service_lower in SKIP_CLASSIFIED_SERVICES:
            continue

        key = service_key(service_lower)
        record_id = str(integration.get("id", "")).strip()

        for instance in _record_instances(integration):
            credentials = instance.get("credentials", {}) or {}
            instance_name = str(instance.get("name", "default")).strip().lower() or "default"
            instance_tags = instance.get("tags", {}) or {}
            flat_view, flat_key = _classify_service_instance(key, credentials, record_id=record_id)
            if flat_view is None or flat_key is None:
                continue
            resolved.setdefault(flat_key, flat_view)
            # Bucket under the family key so related classifier outputs (e.g.
            # grafana + grafana_local) share one _all_<family>_instances list.
            all_instances.setdefault(family_key(flat_key), []).append(
                {
                    "name": instance_name,
                    "tags": instance_tags,
                    "config": flat_view,
                    "integration_id": record_id,
                }
            )

    for service, instances in all_instances.items():
        if len(instances) > 1 or (instances and instances[0]["name"] != "default"):
            resolved[f"_all_{service}_instances"] = instances

    resolved["_all"] = active
    return resolved


_ClassifyFn = Callable[[dict[str, Any], str], tuple[Any | None, str | None]]


_CLASSIFIERS: dict[str, _ClassifyFn] = {
    "grafana": _classify_grafana,
    "grafana_local": _classify_grafana,
    "aws": _classify_aws,
    "datadog": _classify_datadog,
    "groundcover": _classify_groundcover,
    "honeycomb": _classify_honeycomb,
    "coralogix": _classify_coralogix,
    "github": _classify_github,
    "sentry": _classify_sentry,
    "gitlab": _classify_gitlab,
    "jenkins": _classify_jenkins,
    "mongodb": _classify_mongodb,
    "redis": _classify_redis,
    "postgresql": _classify_postgresql,
    "mongodb_atlas": _classify_mongodb_atlas,
    "mariadb": _classify_mariadb,
    "vercel": _classify_vercel,
    "opsgenie": _classify_opsgenie,
    "pagerduty": _classify_pagerduty,
    "incident_io": _classify_incident_io,
    "jira": _classify_jira,
    "discord": _classify_discord,
    "telegram": _classify_telegram,
    "whatsapp": _classify_whatsapp,
    "twilio": _classify_twilio,
    "openclaw": _classify_openclaw,
    "posthog_mcp": _classify_posthog_mcp,
    "sentry_mcp": _classify_sentry_mcp,
    "mysql": _classify_mysql,
    "dagster": _classify_dagster,
    "rabbitmq": _classify_rabbitmq,
    "rds": _classify_rds,
    "airflow": _classify_airflow,
    "betterstack": _classify_betterstack,
    "azure_sql": _classify_azure_sql,
    "alertmanager": _classify_alertmanager,
    "argocd": _classify_argocd,
    "helm": _classify_helm,
    "victoria_logs": _classify_victoria_logs,
    "bitbucket": _classify_bitbucket,
    "snowflake": _classify_snowflake,
    "azure": _classify_azure,
    "openobserve": _classify_openobserve,
    "opensearch": _classify_opensearch,
    "splunk": _classify_splunk,
    "supabase": _classify_supabase,
    "signoz": _classify_signoz,
    "tempo": _classify_tempo,
    "temporal": _classify_temporal,
    "smtp": _classify_smtp,
}


def _classify_service_instance(
    key: str, credentials: dict[str, Any], *, record_id: str
) -> tuple[Any | None, str | None]:
    """Classify one instance into (flat_view, resolved_key).

    Returns ``(None, None)`` when the instance is invalid or should be skipped
    (e.g. required field missing). The returned ``resolved_key`` is usually
    ``key`` itself, but Grafana splits into ``grafana`` or ``grafana_local``
    based on its ``is_local`` property.
    """
    handler = _CLASSIFIERS.get(key)
    if handler is not None:
        return handler(credentials, record_id)
    # Fallback for unknown services: pass through credentials + record id.
    return {"credentials": credentials, "integration_id": record_id}, key


def _parse_instances_env(env_name: str, service: str) -> dict[str, Any] | None:
    """Parse ``<SERVICE>_INSTANCES`` env var into a v2 integration record.

    Accepts a JSON array of instance entries. Each entry may be either
    ``{"name": ..., "tags": {...}, "credentials": {...}}`` or a flat
    ``{"name": ..., "tags": {...}, <field>: <value>, ...}`` — we accept
    both shapes and normalize to ``credentials``. Returns None if the env
    var is unset, empty, invalid JSON, or not a non-empty list (logs a
    warning on parse failure so callers can fall through to legacy vars).

    Critical: always returns a SINGLE record with multiple instances inside,
    never multiple records — otherwise ``merge_integrations_by_service``
    would drop all but one (PR #527 bug #2).
    """
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Do NOT include exc.msg or the raw value — JSONDecodeError messages
        # embed a slice of the offending input, which could leak a fragment
        # of an API key if the env var was accidentally populated with a
        # credential instead of a JSON array. Log only position + line/col.
        logger.warning(
            "%s is not valid JSON (parse failed at line %d col %d); falling back to legacy vars",
            env_name,
            exc.lineno,
            exc.colno,
        )
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    instances: list[dict[str, Any]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        nested_creds = entry.get("credentials")
        if isinstance(nested_creds, dict):
            credentials = dict(nested_creds)
        else:
            credentials = {k: v for k, v in entry.items() if k not in {"name", "tags"}}
        name = str(entry.get("name", "default")).strip().lower() or "default"
        tags = entry.get("tags") if isinstance(entry.get("tags"), dict) else {}
        instances.append({"name": name, "tags": tags, "credentials": credentials})
    if not instances:
        return None
    return {
        "id": f"env-{service}",
        "service": service,
        "status": "active",
        "instances": instances,
    }


def _active_env_record(
    service: str,
    credentials: dict[str, Any],
    *,
    record_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": record_id or f"env-{service.replace('_', '-')}",
        "service": service,
        "status": "active",
        **extra,
        "credentials": credentials,
    }


def load_env_integrations() -> list[dict[str, Any]]:
    """Build integration records from local environment variables."""
    integrations: list[dict[str, Any]] = []

    grafana_multi = _parse_instances_env("GRAFANA_INSTANCES", "grafana")
    if grafana_multi is not None:
        integrations.append(grafana_multi)
        grafana_endpoint = ""
        grafana_api_key = ""
    else:
        grafana_endpoint = os.getenv("GRAFANA_INSTANCE_URL", "").strip()
        grafana_api_key = os.getenv("GRAFANA_READ_TOKEN", "").strip()
    if grafana_endpoint and grafana_api_key:
        grafana_config = GrafanaIntegrationConfig.model_validate(
            {
                "endpoint": grafana_endpoint,
                "api_key": grafana_api_key,
            }
        )
        integrations.append(
            _active_env_record(
                "grafana",
                {
                    "endpoint": grafana_config.endpoint,
                    "api_key": grafana_config.api_key,
                },
            )
        )

    datadog_multi = _parse_instances_env("DD_INSTANCES", "datadog")
    if datadog_multi is not None:
        integrations.append(datadog_multi)
        datadog_api_key = ""
        datadog_app_key = ""
        datadog_site = ""
    else:
        datadog_api_key = os.getenv("DD_API_KEY", "").strip()
        datadog_app_key = os.getenv("DD_APP_KEY", "").strip()
        datadog_site = os.getenv("DD_SITE", "datadoghq.com").strip() or "datadoghq.com"
    if datadog_api_key and datadog_app_key:
        datadog_config = DatadogIntegrationConfig.model_validate(
            {
                "api_key": datadog_api_key,
                "app_key": datadog_app_key,
                "site": datadog_site,
            }
        )
        integrations.append(
            _active_env_record(
                "datadog",
                datadog_config.model_dump(exclude={"integration_id"}),
            )
        )

    groundcover_multi = _parse_instances_env("GROUNDCOVER_INSTANCES", "groundcover")
    if groundcover_multi is not None:
        integrations.append(groundcover_multi)
        groundcover_api_key = ""
    else:
        groundcover_api_key = (
            os.getenv("GROUNDCOVER_API_KEY", "").strip()
            or os.getenv("GROUNDCOVER_MCP_TOKEN", "").strip()
        )
    if groundcover_api_key:
        # The groundcover config validates the MCP URL (HTTPS-or-loopback), which
        # can raise on a bad GROUNDCOVER_MCP_URL. Guard it so one malformed value
        # cannot abort discovery of every other env integration.
        try:
            groundcover_config = GroundcoverIntegrationConfig.model_validate(
                {
                    "api_key": groundcover_api_key,
                    "mcp_url": os.getenv("GROUNDCOVER_MCP_URL", "").strip(),
                    "tenant_uuid": os.getenv("GROUNDCOVER_TENANT_UUID", "").strip(),
                    "backend_id": os.getenv("GROUNDCOVER_BACKEND_ID", "").strip(),
                    "timezone": os.getenv("GROUNDCOVER_TIMEZONE", "").strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="groundcover")
        else:
            integrations.append(
                _active_env_record(
                    "groundcover",
                    groundcover_config.model_dump(exclude={"integration_id"}),
                )
            )

    honeycomb_multi = _parse_instances_env("HONEYCOMB_INSTANCES", "honeycomb")
    if honeycomb_multi is not None:
        integrations.append(honeycomb_multi)
        honeycomb_api_key = ""
    else:
        honeycomb_api_key = os.getenv("HONEYCOMB_API_KEY", "").strip()
    if honeycomb_api_key:
        honeycomb_config = HoneycombIntegrationConfig.model_validate(
            {
                "api_key": honeycomb_api_key,
                "dataset": os.getenv("HONEYCOMB_DATASET", "").strip(),
                "base_url": os.getenv("HONEYCOMB_API_URL", "").strip(),
            }
        )
        integrations.append(
            _active_env_record(
                "honeycomb",
                honeycomb_config.model_dump(exclude={"integration_id"}),
            )
        )

    coralogix_multi = _parse_instances_env("CORALOGIX_INSTANCES", "coralogix")
    if coralogix_multi is not None:
        integrations.append(coralogix_multi)
        coralogix_api_key = ""
    else:
        coralogix_api_key = os.getenv("CORALOGIX_API_KEY", "").strip()
    if coralogix_api_key:
        coralogix_config = CoralogixIntegrationConfig.model_validate(
            {
                "api_key": coralogix_api_key,
                "base_url": os.getenv("CORALOGIX_API_URL", "").strip(),
                "application_name": os.getenv("CORALOGIX_APPLICATION_NAME", "").strip(),
                "subsystem_name": os.getenv("CORALOGIX_SUBSYSTEM_NAME", "").strip(),
            }
        )
        integrations.append(
            _active_env_record(
                "coralogix",
                coralogix_config.model_dump(exclude={"integration_id"}),
            )
        )

    aws_multi = _parse_instances_env("AWS_INSTANCES", "aws")
    if aws_multi is not None:
        integrations.append(aws_multi)
        aws_role_arn = ""
        aws_external_id = ""
        aws_region = "us-east-1"
        aws_access_key_id = ""
        aws_secret_access_key = ""
        aws_session_token = ""
    else:
        aws_role_arn = os.getenv("AWS_ROLE_ARN", "").strip()
        aws_external_id = os.getenv("AWS_EXTERNAL_ID", "").strip()
        aws_region = os.getenv("AWS_REGION", "us-east-1").strip() or "us-east-1"
        aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
        aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
        aws_session_token = os.getenv("AWS_SESSION_TOKEN", "").strip()
    if aws_role_arn:
        aws_config = AWSIntegrationConfig.model_validate(
            {
                "role_arn": aws_role_arn,
                "external_id": aws_external_id,
                "region": aws_region,
            }
        )
        integrations.append(
            _active_env_record(
                "aws",
                {"region": aws_config.region},
                role_arn=aws_config.role_arn,
                external_id=aws_config.external_id,
            )
        )
    elif aws_access_key_id and aws_secret_access_key:
        aws_config = AWSIntegrationConfig.model_validate(
            {
                "region": aws_region,
                "credentials": {
                    "access_key_id": aws_access_key_id,
                    "secret_access_key": aws_secret_access_key,
                    "session_token": aws_session_token,
                },
            }
        )
        aws_credentials = aws_config.credentials
        if aws_credentials is not None:
            integrations.append(
                _active_env_record(
                    "aws",
                    {
                        "access_key_id": aws_credentials.access_key_id,
                        "secret_access_key": aws_credentials.secret_access_key,
                        "session_token": aws_credentials.session_token,
                        "region": aws_config.region,
                    },
                )
            )

    github_mode = os.getenv("GITHUB_MCP_MODE", "streamable-http").strip() or "streamable-http"
    github_url = os.getenv("GITHUB_MCP_URL", "").strip()
    github_command = os.getenv("GITHUB_MCP_COMMAND", "").strip()
    github_args = os.getenv("GITHUB_MCP_ARGS", "").strip()
    github_auth_token = os.getenv("GITHUB_MCP_AUTH_TOKEN", "").strip()
    github_toolsets = os.getenv("GITHUB_MCP_TOOLSETS", "").strip()
    if (github_mode == "stdio" and github_command) or (github_mode != "stdio" and github_url):
        github_config = build_github_mcp_config(
            {
                "url": github_url,
                "mode": github_mode,
                "command": github_command,
                "args": [part for part in github_args.split() if part],
                "auth_token": github_auth_token,
                "toolsets": [part.strip() for part in github_toolsets.split(",") if part.strip()],
            }
        )
        integrations.append(
            _active_env_record(
                "github",
                github_config.model_dump(exclude={"integration_id"}),
            )
        )

    sentry_org_slug = os.getenv("SENTRY_ORG_SLUG", "").strip()
    sentry_auth_token = os.getenv("SENTRY_AUTH_TOKEN", "").strip()
    if sentry_org_slug and sentry_auth_token:
        sentry_config = build_sentry_config(
            {
                "base_url": os.getenv("SENTRY_URL", "https://sentry.io").strip()
                or "https://sentry.io",
                "organization_slug": sentry_org_slug,
                "auth_token": sentry_auth_token,
                "project_slug": os.getenv("SENTRY_PROJECT_SLUG", "").strip(),
            }
        )
        integrations.append(
            _active_env_record(
                "sentry",
                sentry_config.model_dump(exclude={"integration_id"}),
            )
        )

    gitlab_access_token = resolve_env_credential("GITLAB_ACCESS_TOKEN")
    if gitlab_access_token:
        gitlab_config = build_gitlab_config(
            {
                "base_url": os.getenv("GITLAB_BASE_URL", DEFAULT_GITLAB_BASE_URL).strip()
                or DEFAULT_GITLAB_BASE_URL,
                "auth_token": gitlab_access_token,
            }
        )
        integrations.append(_active_env_record("gitlab", gitlab_config.model_dump()))

    mongodb_connection_string = os.getenv("MONGODB_CONNECTION_STRING", "").strip()
    if mongodb_connection_string:
        mongodb_config = build_mongodb_config(
            {
                "connection_string": mongodb_connection_string,
                "database": os.getenv("MONGODB_DATABASE", "").strip(),
                "auth_source": os.getenv("MONGODB_AUTH_SOURCE", "admin").strip() or "admin",
                "tls": os.getenv("MONGODB_TLS", "true").strip().lower() in ("true", "1", "yes"),
            }
        )
        integrations.append(
            _active_env_record(
                "mongodb",
                mongodb_config.model_dump(exclude={"integration_id"}),
            )
        )

    redis_config = redis_config_from_env()
    if redis_config:
        integrations.append(
            _active_env_record(
                "redis",
                redis_config.model_dump(exclude={"integration_id"}),
            )
        )

    postgresql_host = os.getenv("POSTGRESQL_HOST", "").strip()
    postgresql_database = os.getenv("POSTGRESQL_DATABASE", "").strip()
    if postgresql_host and postgresql_database:
        postgresql_config = build_postgresql_config(
            {
                "host": postgresql_host,
                "port": int(_pg_port)
                if (_pg_port := os.getenv("POSTGRESQL_PORT", "").strip()) and _pg_port.isdigit()
                else 5432,
                "database": postgresql_database,
                "username": os.getenv("POSTGRESQL_USERNAME", "postgres").strip() or "postgres",
                "password": os.getenv("POSTGRESQL_PASSWORD", "").strip(),
                "ssl_mode": os.getenv("POSTGRESQL_SSL_MODE", "prefer").strip() or "prefer",
            }
        )
        integrations.append(
            _active_env_record(
                "postgresql",
                postgresql_config.model_dump(exclude={"integration_id"}),
            )
        )

    argocd_multi = _parse_instances_env("ARGOCD_INSTANCES", "argocd")
    if argocd_multi is not None:
        integrations.append(argocd_multi)
        argocd_base_url = ""
        argocd_auth_token = ""
        argocd_username = ""
        argocd_password = ""
    else:
        argocd_base_url = os.getenv("ARGOCD_BASE_URL", "").strip()
        argocd_auth_token = os.getenv("ARGOCD_AUTH_TOKEN", os.getenv("ARGOCD_TOKEN", "")).strip()
        argocd_username = os.getenv("ARGOCD_USERNAME", "").strip()
        argocd_password = os.getenv("ARGOCD_PASSWORD", "").strip()
    if argocd_base_url and (argocd_auth_token or (argocd_username and argocd_password)):
        try:
            argocd_config = ArgoCDIntegrationConfig.model_validate(
                {
                    "base_url": argocd_base_url,
                    "bearer_token": argocd_auth_token,
                    "username": argocd_username,
                    "password": argocd_password,
                    "project": os.getenv("ARGOCD_PROJECT", "").strip(),
                    "app_namespace": os.getenv("ARGOCD_APP_NAMESPACE", "").strip(),
                    "verify_ssl": os.getenv("ARGOCD_VERIFY_SSL", "true").strip(),
                }
            )
        except Exception as exc:
            # Invalid env-derived config: skip ArgoCD entry rather than fail
            # discovery, but report so operators can see the misconfig.
            _report_env_loader_failure(exc, integration="argocd")
        else:
            integrations.append(
                _active_env_record(
                    "argocd",
                    argocd_config.model_dump(exclude={"integration_id"}),
                )
            )

    helm_env_enabled = os.getenv("OSRE_HELM_INTEGRATION", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if helm_env_enabled:
        try:
            helm_env_config = HelmIntegrationConfig.model_validate(
                {
                    "helm_path": os.getenv("HELM_PATH", "helm").strip() or "helm",
                    "kube_context": os.getenv("HELM_KUBE_CONTEXT", "").strip(),
                    "kubeconfig": os.getenv("HELM_KUBECONFIG", "").strip(),
                    "default_namespace": os.getenv("HELM_NAMESPACE", "").strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="helm")
        else:
            integrations.append(
                _active_env_record(
                    "helm",
                    helm_env_config.model_dump(exclude={"integration_id"}),
                )
            )

    vercel_api_token = os.getenv("VERCEL_API_TOKEN", "").strip()
    if vercel_api_token:
        try:
            vercel_config = VercelConfig.model_validate(
                {
                    "api_token": vercel_api_token,
                    "team_id": os.getenv("VERCEL_TEAM_ID", "").strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="vercel")
        else:
            integrations.append(
                _active_env_record(
                    "vercel",
                    vercel_config.model_dump(exclude={"integration_id"}),
                )
            )

    opsgenie_api_key = os.getenv("OPSGENIE_API_KEY", "").strip()
    if opsgenie_api_key:
        try:
            opsgenie_config = OpsGenieIntegrationConfig.model_validate(
                {
                    "api_key": opsgenie_api_key,
                    "region": os.getenv("OPSGENIE_REGION", "us").strip() or "us",
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="opsgenie")
        else:
            integrations.append(
                _active_env_record(
                    "opsgenie",
                    opsgenie_config.model_dump(exclude={"integration_id"}),
                )
            )

    pagerduty_api_key = os.getenv("PAGERDUTY_API_KEY", "").strip()
    if pagerduty_api_key:
        try:
            _envs: dict[str, Any] = {"api_key": pagerduty_api_key}
            base_url = os.getenv("PAGERDUTY_BASE_URL", "").strip()
            if base_url:
                _envs["base_url"] = base_url
            pagerduty_config = PagerDutyIntegrationConfig.model_validate(_envs)
        except Exception as exc:
            _report_env_loader_failure(exc, integration="pagerduty")
        else:
            integrations.append(
                _active_env_record(
                    "pagerduty",
                    pagerduty_config.model_dump(exclude={"integration_id"}),
                )
            )

    incident_io_api_key = resolve_env_credential("INCIDENT_IO_API_KEY")
    if incident_io_api_key:
        try:
            incident_io_config = IncidentIoIntegrationConfig.model_validate(
                {
                    "api_key": incident_io_api_key,
                    "base_url": os.getenv("INCIDENT_IO_BASE_URL", "").strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="incident_io")
        else:
            integrations.append(
                _active_env_record(
                    "incident_io",
                    incident_io_config.model_dump(exclude={"integration_id"}),
                )
            )

    jira_base_url = os.getenv("JIRA_BASE_URL", "").strip()
    jira_email = os.getenv("JIRA_EMAIL", "").strip()
    jira_api_token = os.getenv("JIRA_API_TOKEN", "").strip()
    jira_project_key = os.getenv("JIRA_PROJECT_KEY", "").strip()
    if jira_base_url and jira_email and jira_api_token:
        try:
            jira_config = JiraIntegrationConfig.model_validate(
                {
                    "base_url": jira_base_url,
                    "email": jira_email,
                    "api_token": jira_api_token,
                    "project_key": jira_project_key,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="jira")
        else:
            integrations.append(
                _active_env_record(
                    "jira",
                    jira_config.model_dump(exclude={"integration_id"}),
                )
            )

    discord_bot_token = resolve_env_credential("DISCORD_BOT_TOKEN")
    if discord_bot_token:
        try:
            discord_config = DiscordBotConfig.model_validate(
                {
                    "bot_token": discord_bot_token,
                    "application_id": os.getenv("DISCORD_APPLICATION_ID", "").strip(),
                    "public_key": os.getenv("DISCORD_PUBLIC_KEY", "").strip(),
                    "default_channel_id": os.getenv("DISCORD_DEFAULT_CHANNEL_ID", "").strip()
                    or None,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="discord")
        else:
            integrations.append(_active_env_record("discord", discord_config.model_dump()))

    airflow_config = airflow_config_from_env()
    if airflow_config is not None:
        integrations.append(_active_env_record("airflow", airflow_config.model_dump()))

    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if telegram_bot_token:
        try:
            tg_config = TelegramBotConfig.model_validate(
                {
                    "bot_token": telegram_bot_token,
                    "default_chat_id": os.getenv("TELEGRAM_DEFAULT_CHAT_ID", "").strip() or None,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="telegram")
        else:
            integrations.append(_active_env_record("telegram", tg_config.model_dump()))

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    if smtp_host:
        try:
            smtp_config = SMTPIntegrationConfig.model_validate(
                {
                    "host": smtp_host,
                    "port": os.getenv("SMTP_PORT", "").strip() or 587,
                    "security": os.getenv("SMTP_SECURITY", "").strip() or "starttls",
                    "username": os.getenv("SMTP_USERNAME", "").strip(),
                    "password": resolve_env_credential("SMTP_PASSWORD"),
                    "from_address": os.getenv("SMTP_FROM_ADDRESS", "").strip(),
                    "default_to": os.getenv("SMTP_DEFAULT_TO", "").strip() or None,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="smtp")
        else:
            integrations.append(_active_env_record("smtp", smtp_config.model_dump()))

    # Shared Twilio account credentials — consumed by both the WhatsApp and
    # the SMS env-bootstrap blocks below.
    twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()

    whatsapp_from_number = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()
    if twilio_account_sid and twilio_auth_token and whatsapp_from_number:
        wa_config = WhatsAppConfig.model_validate(
            {
                "account_sid": twilio_account_sid,
                "auth_token": twilio_auth_token,
                "from_number": whatsapp_from_number,
                "default_to": os.getenv("WHATSAPP_DEFAULT_TO", "").strip() or None,
            }
        )
        integrations.append(_active_env_record("whatsapp", wa_config.model_dump()))

    # Twilio SMS integration — independent of the legacy WhatsApp record.
    # Hydrated when account+token are present AND an SMS sender is set
    # (a from_number or a Messaging Service SID).
    twilio_sms_from = os.getenv("TWILIO_SMS_FROM", "").strip()
    twilio_sms_messaging_service = os.getenv("TWILIO_SMS_MESSAGING_SERVICE_SID", "").strip()
    if (
        twilio_account_sid
        and twilio_auth_token
        and (twilio_sms_from or twilio_sms_messaging_service)
    ):
        twilio_payload: dict[str, Any] = {
            "account_sid": twilio_account_sid,
            "auth_token": twilio_auth_token,
            "sms": {
                "enabled": True,
                "from_number": twilio_sms_from,
                "messaging_service_sid": twilio_sms_messaging_service,
                "default_to": os.getenv("TWILIO_SMS_DEFAULT_TO", "").strip() or None,
            },
        }
        try:
            twilio_config = TwilioIntegrationConfig.model_validate(twilio_payload)
        except Exception:
            twilio_config = None
        if twilio_config is not None:
            integrations.append(
                _active_env_record(
                    "twilio",
                    twilio_config.model_dump(exclude={"integration_id"}),
                )
            )

    atlas_pub = os.getenv("MONGODB_ATLAS_PUBLIC_KEY", "").strip()
    atlas_priv = os.getenv("MONGODB_ATLAS_PRIVATE_KEY", "").strip()
    atlas_project = os.getenv("MONGODB_ATLAS_PROJECT_ID", "").strip()
    if atlas_pub and atlas_priv and atlas_project:
        try:
            atlas_config = build_mongodb_atlas_config(
                {
                    "api_public_key": atlas_pub,
                    "api_private_key": atlas_priv,
                    "project_id": atlas_project,
                    "base_url": os.getenv(
                        "MONGODB_ATLAS_BASE_URL", "https://cloud.mongodb.com/api/atlas/v2"
                    ).strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="mongodb_atlas")
        else:
            integrations.append(
                _active_env_record(
                    "mongodb_atlas",
                    atlas_config.model_dump(exclude={"integration_id"}),
                )
            )

    openclaw_url = os.getenv("OPENCLAW_MCP_URL", "").strip()
    openclaw_command = os.getenv("OPENCLAW_MCP_COMMAND", "").strip()
    openclaw_mode = os.getenv("OPENCLAW_MCP_MODE", "streamable-http").strip().lower()
    openclaw_mode = openclaw_mode or "streamable-http"
    if (openclaw_mode == "stdio" and openclaw_command) or (
        openclaw_mode != "stdio" and openclaw_url
    ):
        try:
            openclaw_config = build_openclaw_config(
                {
                    "url": openclaw_url,
                    "mode": openclaw_mode,
                    "command": openclaw_command,
                    "args": [
                        part for part in os.getenv("OPENCLAW_MCP_ARGS", "").strip().split() if part
                    ],
                    "auth_token": resolve_env_credential("OPENCLAW_MCP_AUTH_TOKEN"),
                }
            )
            integrations.append(
                _active_env_record(
                    "openclaw",
                    {
                        **openclaw_config.model_dump(exclude={"integration_id"}),
                        "connection_verified": True,
                    },
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="openclaw")

    posthog_mcp_mode = os.getenv("POSTHOG_MCP_MODE", "streamable-http").strip().lower()
    posthog_mcp_mode = posthog_mcp_mode or "streamable-http"
    posthog_mcp_command = os.getenv("POSTHOG_MCP_COMMAND", "").strip()
    posthog_mcp_token = resolve_env_credential("POSTHOG_MCP_AUTH_TOKEN")
    posthog_mcp_url = os.getenv("POSTHOG_MCP_URL", "").strip()
    if posthog_mcp_mode != "stdio" and posthog_mcp_token and not posthog_mcp_url:
        posthog_mcp_url = DEFAULT_POSTHOG_MCP_URL
    if (posthog_mcp_mode == "stdio" and posthog_mcp_command) or (
        posthog_mcp_mode != "stdio" and posthog_mcp_url and posthog_mcp_token
    ):
        read_only_env = os.getenv("POSTHOG_MCP_READ_ONLY", "").strip().lower()
        read_only = read_only_env not in ("false", "0", "no") if read_only_env else True
        try:
            posthog_mcp_config = build_posthog_mcp_config(
                {
                    "url": posthog_mcp_url,
                    "mode": posthog_mcp_mode,
                    "command": posthog_mcp_command,
                    "args": [
                        part for part in os.getenv("POSTHOG_MCP_ARGS", "").strip().split() if part
                    ],
                    "auth_token": posthog_mcp_token,
                    "organization_id": os.getenv("POSTHOG_MCP_ORGANIZATION_ID", "").strip(),
                    "project_id": os.getenv("POSTHOG_MCP_PROJECT_ID", "").strip(),
                    "features": os.getenv("POSTHOG_MCP_FEATURES", "").strip(),
                    "read_only": read_only,
                }
            )
            integrations.append(
                _active_env_record(
                    "posthog_mcp",
                    {
                        **posthog_mcp_config.model_dump(exclude={"integration_id"}),
                        "connection_verified": True,
                    },
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="posthog_mcp")

    sentry_mcp_mode = os.getenv("SENTRY_MCP_MODE", "streamable-http").strip().lower()
    sentry_mcp_mode = sentry_mcp_mode or "streamable-http"
    sentry_mcp_command = os.getenv("SENTRY_MCP_COMMAND", "").strip()
    sentry_mcp_token = resolve_env_credential("SENTRY_MCP_AUTH_TOKEN")
    sentry_mcp_url = os.getenv("SENTRY_MCP_URL", "").strip()
    if sentry_mcp_mode != "stdio" and sentry_mcp_token and not sentry_mcp_url:
        sentry_mcp_url = DEFAULT_SENTRY_MCP_URL
    if (sentry_mcp_mode == "stdio" and sentry_mcp_command) or (
        sentry_mcp_mode != "stdio" and sentry_mcp_url and sentry_mcp_token
    ):
        try:
            sentry_mcp_config = build_sentry_mcp_config(
                {
                    "url": sentry_mcp_url,
                    "mode": sentry_mcp_mode,
                    "command": sentry_mcp_command,
                    "args": [
                        part for part in os.getenv("SENTRY_MCP_ARGS", "").strip().split() if part
                    ],
                    "auth_token": sentry_mcp_token,
                    "host": os.getenv("SENTRY_MCP_HOST", "").strip(),
                    "organization_slug": os.getenv("SENTRY_MCP_ORGANIZATION_SLUG", "").strip(),
                    "project_slug": os.getenv("SENTRY_MCP_PROJECT_SLUG", "").strip(),
                    "skills": os.getenv("SENTRY_MCP_SKILLS", "").strip(),
                }
            )
            integrations.append(
                _active_env_record(
                    "sentry_mcp",
                    {
                        **sentry_mcp_config.model_dump(exclude={"integration_id"}),
                        "connection_verified": True,
                    },
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="sentry_mcp")

    mariadb_host = os.getenv("MARIADB_HOST", "").strip()
    mariadb_database = os.getenv("MARIADB_DATABASE", "").strip()
    if mariadb_host and mariadb_database:
        try:
            mariadb_config = build_mariadb_config(
                {
                    "host": mariadb_host,
                    "port": os.getenv("MARIADB_PORT", "3306").strip(),
                    "database": mariadb_database,
                    "username": os.getenv("MARIADB_USERNAME", "").strip(),
                    "password": os.getenv("MARIADB_PASSWORD", "").strip(),
                    "ssl": os.getenv("MARIADB_SSL", "true").strip().lower() in ("true", "1", "yes"),
                }
            )
            integrations.append(
                _active_env_record(
                    "mariadb",
                    mariadb_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="mariadb")

    dagster_endpoint = os.getenv("DAGSTER_ENDPOINT", "").strip()
    if dagster_endpoint:
        try:
            dagster_config = build_dagster_config(
                {
                    "endpoint": dagster_endpoint,
                    "api_token": os.getenv("DAGSTER_API_TOKEN", "").strip(),
                }
            )
            integrations.append(
                _active_env_record(
                    "dagster",
                    dagster_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="dagster")

    rabbitmq_host = os.getenv("RABBITMQ_HOST", "").strip()
    rabbitmq_username = os.getenv("RABBITMQ_USERNAME", "").strip()
    if rabbitmq_host and rabbitmq_username:
        try:
            rabbitmq_config = build_rabbitmq_config(
                {
                    "host": rabbitmq_host,
                    "management_port": os.getenv("RABBITMQ_MANAGEMENT_PORT", "15672").strip(),
                    "username": rabbitmq_username,
                    "password": os.getenv("RABBITMQ_PASSWORD", ""),
                    "vhost": os.getenv("RABBITMQ_VHOST", "/").strip(),
                    "ssl": os.getenv("RABBITMQ_SSL", "false").strip().lower()
                    in ("true", "1", "yes"),
                    "verify_ssl": os.getenv("RABBITMQ_VERIFY_SSL", "true").strip().lower()
                    in ("true", "1", "yes"),
                }
            )
            integrations.append(
                _active_env_record(
                    "rabbitmq",
                    rabbitmq_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="rabbitmq")

    try:
        rds_config = rds_config_from_env()
    except Exception as exc:
        rds_config = None
        _report_env_loader_failure(exc, integration="rds")
    if rds_config is not None and rds_config.is_configured:
        integrations.append(
            _active_env_record(
                "rds",
                rds_config.model_dump(exclude={"integration_id"}),
            )
        )

    bs_endpoint = os.getenv("BETTERSTACK_QUERY_ENDPOINT", "").strip()
    bs_username = os.getenv("BETTERSTACK_USERNAME", "").strip()
    if bs_endpoint and bs_username:
        try:
            bs_config = build_betterstack_config(
                {
                    "query_endpoint": bs_endpoint,
                    "username": bs_username,
                    "password": os.getenv("BETTERSTACK_PASSWORD", ""),
                    "sources": os.getenv("BETTERSTACK_SOURCES", ""),
                }
            )
            integrations.append(
                _active_env_record(
                    "betterstack",
                    bs_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="betterstack")

    mysql_host = os.getenv("MYSQL_HOST", "").strip()
    mysql_database = os.getenv("MYSQL_DATABASE", "").strip()
    if mysql_host and mysql_database:
        mysql_config = build_mysql_config(
            {
                "host": mysql_host,
                "port": int(_mysql_port)
                if (_mysql_port := os.getenv("MYSQL_PORT", "").strip()) and _mysql_port.isdigit()
                else 3306,
                "database": mysql_database,
                "username": os.getenv("MYSQL_USERNAME", "root").strip() or "root",
                "password": os.getenv("MYSQL_PASSWORD", "").strip(),
                "ssl_mode": os.getenv("MYSQL_SSL_MODE", "preferred").strip() or "preferred",
            }
        )
        integrations.append(
            _active_env_record(
                "mysql",
                mysql_config.model_dump(exclude={"integration_id"}),
            )
        )

    azure_sql_server = os.getenv("AZURE_SQL_SERVER", "").strip()
    azure_sql_database = os.getenv("AZURE_SQL_DATABASE", "").strip()
    if azure_sql_server and azure_sql_database:
        _az_port = os.getenv("AZURE_SQL_PORT", "").strip()
        azure_sql_config = build_azure_sql_config(
            {
                "server": azure_sql_server,
                "port": int(_az_port) if _az_port and _az_port.isdigit() else 1433,
                "database": azure_sql_database,
                "username": os.getenv("AZURE_SQL_USERNAME", "").strip(),
                "password": os.getenv("AZURE_SQL_PASSWORD", "").strip(),
                "driver": os.getenv("AZURE_SQL_DRIVER", "ODBC Driver 18 for SQL Server").strip(),
                "encrypt": os.getenv("AZURE_SQL_ENCRYPT", "true").strip().lower()
                in ("true", "1", "yes"),
            }
        )
        integrations.append(
            _active_env_record(
                "azure_sql",
                azure_sql_config.model_dump(exclude={"integration_id"}),
            )
        )

    bitbucket_workspace = os.getenv("BITBUCKET_WORKSPACE", "").strip()
    if bitbucket_workspace:
        integrations.append(
            _active_env_record(
                "bitbucket",
                {
                    "workspace": bitbucket_workspace,
                    "username": os.getenv("BITBUCKET_USERNAME", "").strip(),
                    "app_password": os.getenv("BITBUCKET_APP_PASSWORD", "").strip(),
                    "base_url": os.getenv(
                        "BITBUCKET_BASE_URL", "https://api.bitbucket.org/2.0"
                    ).strip()
                    or "https://api.bitbucket.org/2.0",
                    "max_results": safe_int(os.getenv("BITBUCKET_MAX_RESULTS", "25"), 25),
                },
            )
        )

    snowflake_account = (
        os.getenv("SNOWFLAKE_ACCOUNT_IDENTIFIER", "").strip()
        or os.getenv("SNOWFLAKE_ACCOUNT", "").strip()
    )
    snowflake_token = os.getenv("SNOWFLAKE_TOKEN", "").strip()
    if snowflake_account and snowflake_token:
        integrations.append(
            _active_env_record(
                "snowflake",
                {
                    "account_identifier": snowflake_account,
                    "user": os.getenv("SNOWFLAKE_USER", "").strip(),
                    "password": os.getenv("SNOWFLAKE_PASSWORD", "").strip(),
                    "token": snowflake_token,
                    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "").strip(),
                    "role": os.getenv("SNOWFLAKE_ROLE", "").strip(),
                    "database": os.getenv("SNOWFLAKE_DATABASE", "").strip(),
                    "schema": os.getenv("SNOWFLAKE_SCHEMA", "").strip(),
                    "max_results": safe_int(os.getenv("SNOWFLAKE_MAX_RESULTS", "50"), 50),
                },
            )
        )

    azure_workspace_id = os.getenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", "").strip()
    azure_access_token = os.getenv("AZURE_LOG_ANALYTICS_TOKEN", "").strip()
    if azure_workspace_id and azure_access_token:
        integrations.append(
            _active_env_record(
                "azure",
                {
                    "workspace_id": azure_workspace_id,
                    "access_token": azure_access_token,
                    "endpoint": (
                        os.getenv(
                            "AZURE_LOG_ANALYTICS_ENDPOINT", "https://api.loganalytics.io"
                        ).strip()
                        or "https://api.loganalytics.io"
                    ),
                    "tenant_id": os.getenv("AZURE_TENANT_ID", "").strip(),
                    "subscription_id": os.getenv("AZURE_SUBSCRIPTION_ID", "").strip(),
                    "max_results": safe_int(os.getenv("AZURE_MAX_RESULTS", "100"), 100),
                },
            )
        )

    openobserve_url = os.getenv("OPENOBSERVE_URL", "").strip()
    openobserve_token = os.getenv("OPENOBSERVE_TOKEN", "").strip()
    openobserve_username = os.getenv("OPENOBSERVE_USERNAME", "").strip()
    openobserve_password = os.getenv("OPENOBSERVE_PASSWORD", "").strip()
    if openobserve_url and (openobserve_token or (openobserve_username and openobserve_password)):
        integrations.append(
            _active_env_record(
                "openobserve",
                {
                    "base_url": openobserve_url.rstrip("/"),
                    "org": os.getenv("OPENOBSERVE_ORG", "default").strip() or "default",
                    "api_token": openobserve_token,
                    "username": openobserve_username,
                    "password": openobserve_password,
                    "stream": os.getenv("OPENOBSERVE_STREAM", "").strip(),
                    "max_results": safe_int(os.getenv("OPENOBSERVE_MAX_RESULTS", "100"), 100),
                },
            )
        )

    opensearch_url = os.getenv("OPENSEARCH_URL", "").strip()
    if opensearch_url:
        integrations.append(
            _active_env_record(
                "opensearch",
                {
                    "url": opensearch_url.rstrip("/"),
                    "api_key": resolve_env_credential("OPENSEARCH_API_KEY"),
                    "username": os.getenv("OPENSEARCH_USERNAME", "").strip(),
                    "password": resolve_env_credential("OPENSEARCH_PASSWORD"),
                    "index_pattern": os.getenv("OPENSEARCH_INDEX_PATTERN", "*").strip() or "*",
                    "max_results": safe_int(os.getenv("OPENSEARCH_MAX_RESULTS", "100"), 100),
                },
            )
        )

    alertmanager_url = os.getenv("ALERTMANAGER_URL", "").strip().rstrip("/")
    if alertmanager_url:
        try:
            alertmanager_config = AlertmanagerIntegrationConfig.model_validate(
                {
                    "base_url": alertmanager_url,
                    "bearer_token": os.getenv("ALERTMANAGER_BEARER_TOKEN", "").strip(),
                    "username": os.getenv("ALERTMANAGER_USERNAME", "").strip(),
                    "password": os.getenv("ALERTMANAGER_PASSWORD", "").strip(),
                }
            )
            integrations.append(
                _active_env_record(
                    "alertmanager",
                    alertmanager_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="alertmanager")

    victoria_logs_url = os.getenv("VICTORIA_LOGS_URL", "").strip().rstrip("/")
    if victoria_logs_url:
        try:
            victoria_logs_config = VictoriaLogsIntegrationConfig.model_validate(
                {
                    "base_url": victoria_logs_url,
                    "tenant_id": os.getenv("VICTORIA_LOGS_TENANT_ID"),
                }
            )
            integrations.append(
                _active_env_record(
                    "victoria_logs",
                    victoria_logs_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="victoria_logs")

    splunk_multi = _parse_instances_env("SPLUNK_INSTANCES", "splunk")
    if splunk_multi is not None:
        integrations.append(splunk_multi)
    else:
        splunk_url = os.getenv("SPLUNK_URL", "").strip()
        splunk_token = os.getenv("SPLUNK_TOKEN", "").strip()
        if splunk_url and splunk_token:
            splunk_config = SplunkIntegrationConfig.model_validate(
                {
                    "base_url": splunk_url,
                    "token": splunk_token,
                    "index": os.getenv("SPLUNK_INDEX", "main").strip(),
                    "verify_ssl": os.getenv("SPLUNK_VERIFY_SSL", "true").strip().lower() != "false",
                    "ca_bundle": os.getenv("SPLUNK_CA_BUNDLE", "").strip(),
                }
            )
            integrations.append(
                _active_env_record(
                    "splunk",
                    splunk_config.model_dump(exclude={"integration_id"}),
                )
            )

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_service_key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if supabase_url and supabase_service_key:
        try:
            sb_config = build_supabase_config(
                {"url": supabase_url, "service_key": supabase_service_key}
            )
            integrations.append(
                _active_env_record(
                    "supabase",
                    {"project_url": sb_config.url},
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="supabase")

    try:
        signoz_config = signoz_config_from_env()
        if signoz_config is not None and signoz_config.is_configured:
            integrations.append(
                _active_env_record(
                    "signoz",
                    signoz_config.model_dump(exclude={"integration_id"}),
                )
            )
    except Exception:
        logger.debug("Failed to load SigNoz config from env", exc_info=True)

    try:
        jenkins_config = jenkins_config_from_env()
        if jenkins_config is not None and jenkins_config.is_configured:
            integrations.append(
                _active_env_record(
                    "jenkins",
                    jenkins_config.model_dump(exclude={"integration_id"}),
                )
            )
    except Exception:
        logger.debug("Failed to load Jenkins config from env", exc_info=True)

    try:
        tempo_config = tempo_config_from_env()
        if tempo_config is not None and tempo_config.is_configured:
            integrations.append(
                _active_env_record(
                    "tempo",
                    tempo_config.model_dump(exclude={"integration_id"}),
                )
            )
    except Exception:
        logger.debug("Failed to load Tempo config from env", exc_info=True)

    temporal_url = os.getenv("TEMPORAL_API_URL", "").strip()
    temporal_namespace = os.getenv("TEMPORAL_NAMESPACE", "default").strip()
    if temporal_url and temporal_namespace:
        try:
            temporal_config = TemporalConfig.model_validate(
                {
                    "base_url": temporal_url,
                    "api_key": os.getenv("TEMPORAL_API_KEY", "").strip(),
                    "namespace": temporal_namespace,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="temporal")
        else:
            integrations.append(
                _active_env_record(
                    "temporal",
                    temporal_config.model_dump(),
                )
            )

    return integrations


def merge_local_integrations(
    store_integrations: list[dict[str, Any]],
    env_integrations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge local store and env integrations, preferring store entries by service."""
    return merge_integrations_by_service(env_integrations, store_integrations)


def merge_integrations_by_service(
    *integration_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge integration records by service, letting later groups override earlier ones."""
    merged_by_service: dict[str, dict[str, Any]] = {}
    for integration_group in integration_groups:
        for integration in integration_group:
            service = str(integration.get("service", "")).strip()
            if service:
                merged_by_service[service] = integration
    return list(merged_by_service.values())


def _effective_entry(source: str, config: dict[str, Any]) -> dict[str, Any]:
    return {"source": source, "config": config}


def _config_as_dict(config: Any) -> dict[str, Any] | None:
    """Normalize a classified config (BaseModel or dict) to a plain dict."""
    from pydantic import BaseModel

    if isinstance(config, BaseModel):
        return config.model_dump(exclude_none=True)
    if isinstance(config, dict) and config:
        return config
    return None


def _publish_classified_effective_service(
    effective: dict[str, dict[str, Any]],
    classified_integrations: dict[str, Any],
    source_by_service: dict[str, str],
    service: str,
) -> None:
    """Copy a directly classified service into the effective view."""
    resolved_integration = classified_integrations.get(service)
    config_dict = _config_as_dict(resolved_integration)
    if config_dict is None:
        return

    effective[service] = _effective_entry(
        source_by_service.get(service, "local env"),
        config_dict,
    )
    all_instances = classified_integrations.get(f"_all_{service}_instances")
    if _should_publish_instance_siblings(all_instances) and isinstance(all_instances, list):
        # Convert any BaseModel configs to dicts in the instances list
        normalized_instances = [
            {**inst, "config": _config_as_dict(inst.get("config")) or {}}
            if isinstance(inst, dict)
            else inst
            for inst in all_instances
        ]
        effective[service]["instances"] = normalized_instances


def _service_metadata(
    store_integrations: list[dict[str, Any]],
    env_integrations: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    source_by_service: dict[str, str] = {}
    store_integration_by_service: dict[str, dict[str, Any]] = {}

    for integration in env_integrations:
        service = str(integration.get("service", "")).strip().lower()
        if service:
            source_by_service[service] = "local env"

    for integration in store_integrations:
        service = str(integration.get("service", "")).strip().lower()
        if service:
            source_by_service[service] = "local store"
            store_integration_by_service.setdefault(service, integration)

    return source_by_service, store_integration_by_service


def _raw_credentials(config: dict[str, Any]) -> dict[str, Any]:
    credentials = config.get("credentials")
    if isinstance(credentials, dict):
        return credentials

    instances = config.get("instances")
    if isinstance(instances, list):
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            instance_credentials = instance.get("credentials")
            if isinstance(instance_credentials, dict):
                return instance_credentials

    return config


def resolve_effective_integrations(
    *,
    store_integrations: list[dict[str, Any]] | None = None,
    env_integrations: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve effective local integrations from ~/.opensre and environment variables."""
    store_records = (
        list(store_integrations) if store_integrations is not None else load_integrations()
    )
    env_records = (
        list(env_integrations) if env_integrations is not None else load_env_integrations()
    )
    merged_integrations = merge_local_integrations(store_records, env_records)
    classified_integrations = classify_integrations(merged_integrations)
    source_by_service, store_integration_by_service = _service_metadata(store_records, env_records)

    effective: dict[str, dict[str, Any]] = {}

    for service in DIRECT_CLASSIFIED_EFFECTIVE_SERVICES:
        _publish_classified_effective_service(
            effective,
            classified_integrations,
            source_by_service,
            service,
        )

    if "datadog" not in effective:
        datadog_store_integration = store_integration_by_service.get("datadog")
        if isinstance(datadog_store_integration, dict):
            datadog_credentials = _raw_credentials(datadog_store_integration)
            effective["datadog"] = _effective_entry(
                "local store",
                {
                    "api_key": str(datadog_credentials.get("api_key", "")).strip(),
                    "app_key": str(datadog_credentials.get("app_key", "")).strip(),
                    "site": str(datadog_credentials.get("site", "datadoghq.com")).strip()
                    or "datadoghq.com",
                    "integration_id": str(datadog_store_integration.get("id", "")).strip(),
                },
            )

    tracer_integration = classified_integrations.get("tracer")
    if isinstance(tracer_integration, dict):
        tracer_credentials = _raw_credentials(tracer_integration)
        effective["tracer"] = _effective_entry(
            source_by_service.get("tracer", "local store"),
            {
                "base_url": str(tracer_credentials.get("base_url", "")).strip(),
                "jwt_token": str(tracer_credentials.get("jwt_token", "")).strip(),
            },
        )
    else:
        jwt_token = os.getenv("JWT_TOKEN", "").strip()
        if jwt_token:
            effective["tracer"] = _effective_entry(
                "local env",
                {
                    "base_url": os.getenv("TRACER_API_URL", "").strip() or get_tracer_base_url(),
                    "jwt_token": jwt_token,
                },
            )

    slack_store_integration = store_integration_by_service.get("slack")
    if isinstance(slack_store_integration, dict):
        slack_credentials = _raw_credentials(slack_store_integration)
        webhook_url = str(slack_credentials.get("webhook_url", "")).strip()
        if webhook_url:
            try:
                slack_config = SlackWebhookConfig.model_validate({"webhook_url": webhook_url})
                effective["slack"] = _effective_entry("local store", slack_config.model_dump())
            except Exception:
                # Do NOT include the exception value — Pydantic v2 ValidationError
                # embeds the input_value (here a SlackWebhookConfig containing the
                # webhook_url) in its string representation, and Slack webhook URLs
                # carry a secret token in the path. Log only a static message.
                logger.warning("Slack webhook URL from store is invalid; skipping Slack")
    elif slack_webhook_url := os.getenv("SLACK_WEBHOOK_URL", "").strip():
        try:
            slack_config = SlackWebhookConfig.model_validate({"webhook_url": slack_webhook_url})
            effective["slack"] = _effective_entry("local env", slack_config.model_dump())
        except Exception:
            # See note above: avoid logging the ValidationError which embeds the
            # raw webhook_url (and its secret token).
            logger.warning("SLACK_WEBHOOK_URL is invalid; skipping Slack")

    google_docs_integration = classified_integrations.get("google_docs")
    if isinstance(google_docs_integration, dict):
        google_docs_credentials = _raw_credentials(google_docs_integration)
        effective["google_docs"] = _effective_entry(
            source_by_service.get("google_docs", "local env"),
            {
                "credentials_file": str(
                    google_docs_credentials.get("credentials_file", "")
                ).strip(),
                "folder_id": str(google_docs_credentials.get("folder_id", "")).strip(),
            },
        )
    else:
        credentials_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()
        folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
        if credentials_file and folder_id:
            effective["google_docs"] = _effective_entry(
                "local env",
                {
                    "credentials_file": credentials_file,
                    "folder_id": folder_id,
                },
            )

    kafka_integration = classified_integrations.get("kafka")
    if isinstance(kafka_integration, dict):
        kafka_credentials = _raw_credentials(kafka_integration)
        effective["kafka"] = _effective_entry(
            source_by_service.get("kafka", "local env"),
            {
                "bootstrap_servers": str(kafka_credentials.get("bootstrap_servers", "")).strip(),
                "security_protocol": str(
                    kafka_credentials.get("security_protocol", "PLAINTEXT")
                ).strip(),
                "sasl_mechanism": str(kafka_credentials.get("sasl_mechanism", "")).strip(),
                "sasl_username": str(kafka_credentials.get("sasl_username", "")).strip(),
                "sasl_password": str(kafka_credentials.get("sasl_password", "")).strip(),
            },
        )
    else:
        kafka_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip()
        if kafka_servers:
            effective["kafka"] = _effective_entry(
                "local env",
                {
                    "bootstrap_servers": kafka_servers,
                    "security_protocol": os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT").strip(),
                    "sasl_mechanism": os.getenv("KAFKA_SASL_MECHANISM", "").strip(),
                    "sasl_username": os.getenv("KAFKA_SASL_USERNAME", "").strip(),
                    "sasl_password": os.getenv("KAFKA_SASL_PASSWORD", "").strip(),
                },
            )

    clickhouse_integration = classified_integrations.get("clickhouse")
    if isinstance(clickhouse_integration, dict):
        clickhouse_credentials = _raw_credentials(clickhouse_integration)
        effective["clickhouse"] = _effective_entry(
            source_by_service.get("clickhouse", "local env"),
            {
                "host": str(clickhouse_credentials.get("host", "")).strip(),
                "port": clickhouse_credentials.get("port", 8123),
                "database": str(clickhouse_credentials.get("database", "default")).strip(),
                "username": str(clickhouse_credentials.get("username", "default")).strip(),
                "password": str(clickhouse_credentials.get("password", "")).strip(),
                "secure": clickhouse_credentials.get("secure", False),
            },
        )
    else:
        clickhouse_host = os.getenv("CLICKHOUSE_HOST", "").strip()
        if clickhouse_host:
            effective["clickhouse"] = _effective_entry(
                "local env",
                {
                    "host": clickhouse_host,
                    "port": int(os.getenv("CLICKHOUSE_PORT", "8123") or "8123"),
                    "database": os.getenv("CLICKHOUSE_DATABASE", "default").strip(),
                    "username": os.getenv("CLICKHOUSE_USER", "default").strip(),
                    "password": os.getenv("CLICKHOUSE_PASSWORD", "").strip(),
                    "secure": os.getenv("CLICKHOUSE_SECURE", "false").strip().lower()
                    in ("true", "1", "yes"),
                },
            )

    known_keys = set(EffectiveIntegrations.model_fields)
    unknown_keys = set(effective) - known_keys
    if unknown_keys:
        logger.warning(
            "resolve_effective_integrations: dropping unrecognised integration key(s): %s",
            sorted(unknown_keys),
        )
    filtered_effective = {k: v for k, v in effective.items() if k in known_keys}
    return EffectiveIntegrations.model_validate(filtered_effective).model_dump(exclude_none=True)
