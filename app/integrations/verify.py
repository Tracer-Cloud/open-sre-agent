"""Compatibility verification facade built on top of shared integration adapters."""

from __future__ import annotations

from typing import Any, cast

import boto3  # noqa: F401
import httpx
import requests  # noqa: F401

from app.auth.jwt_auth import extract_org_id_from_jwt
from app.integrations import _verification_adapters as _adapters
from app.integrations.catalog import (
    resolve_effective_integrations as _resolve_effective_integrations,
)
from app.integrations.config_models import SlackWebhookConfig
from app.integrations.github_mcp import (  # noqa: F401
    build_github_mcp_config,
    validate_github_mcp_config,
)
from app.integrations.probes import ProbeResult
from app.integrations.registry import CORE_VERIFY_SERVICES, SUPPORTED_VERIFY_SERVICES
from app.integrations.sentry import build_sentry_config, validate_sentry_config  # noqa: F401
from app.services.argocd import ArgoCDClient, ArgoCDConfig
from app.services.coralogix import CoralogixClient  # noqa: F401
from app.services.datadog.client import DatadogClient  # noqa: F401
from app.services.honeycomb import HoneycombClient  # noqa: F401
from app.services.tracer_client.client import TracerClient
from app.services.vercel.client import VercelClient, VercelConfig

VerifierFn = _adapters.VerifierFn
_result = _adapters.result
(
    _verify_alertmanager,
    _verify_aws,
    _verify_azure,
    _verify_azure_sql,
    _verify_betterstack,
    _verify_bitbucket,
    _verify_clickhouse,
    _verify_coralogix,
    _verify_datadog,
    _verify_discord,
    _verify_google_docs,
    _verify_grafana,
    _verify_honeycomb,
    _verify_kafka,
    _verify_mariadb,
    _verify_mongodb,
    _verify_mongodb_atlas,
    _verify_mysql,
    _verify_openclaw,
    _verify_openobserve,
    _verify_opensearch,
    _verify_opsgenie,
    _verify_postgresql,
    _verify_rabbitmq,
    _verify_snowflake,
    _verify_splunk,
    _verify_telegram,
) = (
    _adapters._verify_alertmanager,
    _adapters._verify_aws,
    _adapters._verify_azure,
    _adapters._verify_azure_sql,
    _adapters._verify_betterstack,
    _adapters._verify_bitbucket,
    _adapters._verify_clickhouse,
    _adapters._verify_coralogix,
    _adapters._verify_datadog,
    _adapters._verify_discord,
    _adapters._verify_google_docs,
    _adapters._verify_grafana,
    _adapters._verify_honeycomb,
    _adapters._verify_kafka,
    _adapters._verify_mariadb,
    _adapters._verify_mongodb,
    _adapters._verify_mongodb_atlas,
    _adapters._verify_mysql,
    _adapters._verify_openclaw,
    _adapters._verify_openobserve,
    _adapters._verify_opensearch,
    _adapters._verify_opsgenie,
    _adapters._verify_postgresql,
    _adapters._verify_rabbitmq,
    _adapters._verify_snowflake,
    _adapters._verify_splunk,
    _adapters._verify_telegram,
)


def resolve_effective_integrations() -> dict[str, dict[str, Any]]:
    """Resolve effective local integrations from ~/.tracer and environment variables."""
    return _resolve_effective_integrations()


def _run_validation_verifier(
    service: str,
    source: str,
    config: dict[str, Any],
    *,
    build_config: Any,
    validate_config: Any,
) -> dict[str, str]:
    verifier = _adapters.build_validation_verifier(
        service,
        build_config=build_config,
        validate_config=validate_config,
    )
    return verifier(source, config)


def _run_probe_verifier(
    service: str,
    source: str,
    config: dict[str, Any],
    *,
    build_config: Any,
    client_factory: Any,
) -> dict[str, str]:
    verifier = _adapters.build_probe_verifier(
        service,
        build_config=build_config,
        client_factory=client_factory,
    )
    return verifier(source, config)


class _CompatVercelProbeClient:
    """Adapter-side probe shim that preserves legacy ``VercelClient`` monkeypatches."""

    def __init__(self, config: VercelConfig) -> None:
        self._client = cast(Any, VercelClient(config))

    def probe_access(self) -> ProbeResult:
        if hasattr(self._client, "probe_access"):
            return cast(ProbeResult, self._client.probe_access())

        with self._client:
            result = self._client.list_projects()
        if not result.get("success"):
            return ProbeResult.failed(
                f"Vercel project list failed: {result.get('error', 'unknown error')}"
            )

        total = int(result.get("total", 0) or 0)
        return ProbeResult.passed(
            f"Connected to Vercel API and listed {total} project(s).",
            total=total,
        )


class _CompatArgoCDProbeClient:
    """Adapter-side probe shim that preserves legacy ``ArgoCDClient`` monkeypatches."""

    def __init__(self, config: ArgoCDConfig) -> None:
        self.config = config
        self._client = cast(Any, ArgoCDClient(config))

    def probe_access(self) -> ProbeResult:
        if hasattr(self._client, "probe_access"):
            return cast(ProbeResult, self._client.probe_access())
        if not self.config.base_url:
            return ProbeResult.missing("Missing base_url.")
        if not (self.config.bearer_token or (self.config.username and self.config.password)):
            return ProbeResult.missing("Missing bearer token or username/password credentials.")

        with self._client:
            projects = [self.config.project] if self.config.project else None
            result = self._client.list_applications(projects=projects)
        if not result.get("success"):
            return ProbeResult.failed(
                f"Application list failed: {result.get('error', 'unknown error')}"
            )

        total = int(result.get("total", 0) or 0)
        suffix = "application" if total == 1 else "applications"
        return ProbeResult.passed(
            f"Connected to Argo CD and listed {total} {suffix}.",
            total=total,
        )


def _verify_slack(
    source: str,
    config: dict[str, object],
    *,
    send_slack_test: bool,
) -> dict[str, str]:
    try:
        webhook_url = SlackWebhookConfig.model_validate(config).webhook_url
    except Exception:
        return _result("slack", source, "missing", "SLACK_WEBHOOK_URL is not configured.")

    if not send_slack_test:
        return _result("slack", source, "configured", "Incoming webhook configured.")

    try:
        response = httpx.post(
            webhook_url,
            json={"text": "Tracer Flow B connectivity test from local CLI."},
            timeout=10.0,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _result("slack", source, "failed", f"Webhook post failed: {exc}")

    return _result(
        "slack",
        source,
        "passed",
        "Posted a test message through the configured incoming webhook.",
    )


def _verify_argocd(source: str, config: dict[str, Any]) -> dict[str, str]:
    return _run_probe_verifier(
        "argocd",
        source,
        config,
        build_config=ArgoCDConfig.model_validate,
        client_factory=_CompatArgoCDProbeClient,
    )


def _verify_github(source: str, config: dict[str, Any]) -> dict[str, str]:
    return _run_validation_verifier(
        "github",
        source,
        config,
        build_config=build_github_mcp_config,
        validate_config=validate_github_mcp_config,
    )


def _verify_sentry(source: str, config: dict[str, Any]) -> dict[str, str]:
    return _run_validation_verifier(
        "sentry",
        source,
        config,
        build_config=build_sentry_config,
        validate_config=validate_sentry_config,
    )


def _verify_tracer(source: str, config: dict[str, Any]) -> dict[str, str]:
    original_extract_org_id = _adapters.extract_org_id_from_jwt
    original_tracer_client = _adapters.TracerClient
    adapter_globals = vars(_adapters)
    try:
        adapter_globals["extract_org_id_from_jwt"] = extract_org_id_from_jwt
        adapter_globals["TracerClient"] = TracerClient
        return cast(dict[str, str], _adapters._verify_tracer(source, config))
    finally:
        adapter_globals["extract_org_id_from_jwt"] = original_extract_org_id
        adapter_globals["TracerClient"] = original_tracer_client


def _verify_vercel(source: str, config: dict[str, Any]) -> dict[str, str]:
    return _run_probe_verifier(
        "vercel",
        source,
        config,
        build_config=VercelConfig.model_validate,
        client_factory=_CompatVercelProbeClient,
    )


def _default_verifier(service: str) -> VerifierFn:
    if service == "slack":
        return lambda source, config: _verify_slack(source, config, send_slack_test=False)
    return cast(VerifierFn, globals()[f"_verify_{service}"])


VERIFIER_REGISTRY = {service: _default_verifier(service) for service in SUPPORTED_VERIFY_SERVICES}


def verify_integrations(
    service: str | None = None,
    *,
    send_slack_test: bool = False,
) -> list[dict[str, str]]:
    """Run verification checks for configured integrations."""
    effective_integrations = resolve_effective_integrations()
    services = [service] if service else list(SUPPORTED_VERIFY_SERVICES)
    results: list[dict[str, str]] = []

    for current_service in services:
        verifier = VERIFIER_REGISTRY.get(current_service)
        if verifier is None:
            results.append(
                _result(
                    current_service,
                    "-",
                    "failed",
                    "Verification is not supported for this service.",
                )
            )
            continue

        integration = effective_integrations.get(current_service)
        if current_service == "slack":
            if not integration:
                results.append(
                    _result("slack", "-", "missing", "SLACK_WEBHOOK_URL is not configured.")
                )
                continue
            results.append(
                _verify_slack(
                    source=str(integration["source"]),
                    config=dict(integration["config"]),
                    send_slack_test=send_slack_test,
                )
            )
            continue

        if not integration:
            results.append(
                _result(current_service, "-", "missing", "Not configured in local store or env.")
            )
            continue

        results.append(verifier(str(integration["source"]), dict(integration["config"])))

    return results


def format_verification_results(results: list[dict[str, str]]) -> str:
    """Render verification results as a compact terminal table."""
    lines = ["", "  SERVICE    SOURCE       STATUS      DETAIL"]
    for row in results:
        lines.append(f"  {row['service']:<10}{row['source']:<13}{row['status']:<12}{row['detail']}")
    lines.append("")
    return "\n".join(lines)


def verification_exit_code(
    results: list[dict[str, str]],
    *,
    requested_service: str | None = None,
) -> int:
    """Return a CLI exit code for a verification run."""
    if any(row["status"] == "failed" for row in results):
        return 1
    if requested_service:
        return 1 if any(row["status"] in {"missing", "failed"} for row in results) else 0
    core_results = [row for row in results if row["service"] in CORE_VERIFY_SERVICES]
    if not any(row["status"] == "passed" for row in core_results):
        return 1
    return 0


__all__ = [
    "CORE_VERIFY_SERVICES",
    "SUPPORTED_VERIFY_SERVICES",
    "VERIFIER_REGISTRY",
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
    "_verify_snowflake",
    "_verify_splunk",
    "_verify_telegram",
    "_verify_tracer",
    "_verify_vercel",
    "format_verification_results",
    "resolve_effective_integrations",
    "verification_exit_code",
    "verify_integrations",
]
