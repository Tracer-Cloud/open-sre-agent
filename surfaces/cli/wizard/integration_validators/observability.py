"""Client-backed validators for log/metric/trace backends."""

from __future__ import annotations

from integrations.config_models import (
    CoralogixIntegrationConfig,
    GrafanaIntegrationConfig,
    HoneycombIntegrationConfig,
)
from integrations.coralogix.client import CoralogixClient
from integrations.datadog.client import DatadogClient, DatadogConfig
from integrations.elasticsearch.client import ElasticsearchClient, ElasticsearchConfig
from integrations.grafana.client import get_grafana_client_from_credentials
from integrations.honeycomb.client import HoneycombClient
from integrations.splunk.client import SplunkClient, SplunkConfig
from integrations.tempo import build_tempo_config, validate_tempo_config

from .shared import IntegrationHealthResult


def validate_grafana_integration(*, endpoint: str, api_key: str) -> IntegrationHealthResult:
    """Validate Grafana credentials by discovering datasource UIDs."""
    try:
        grafana_config = GrafanaIntegrationConfig.model_validate(
            {"endpoint": endpoint, "api_key": api_key}
        )
        client = get_grafana_client_from_credentials(
            endpoint=grafana_config.endpoint,
            api_key=grafana_config.api_key,
            account_id="opensre_onboard_probe",
        )
        discovered = client.discover_datasource_uids()
        if not discovered:
            return IntegrationHealthResult(
                ok=False,
                detail="Grafana is reachable, but no datasources could be discovered with this token.",
            )

        available = ", ".join(sorted(discovered))
        return IntegrationHealthResult(
            ok=True,
            detail=f"Grafana validated with datasource discovery: {available}.",
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Grafana validation failed: {err}")


def validate_datadog_integration(
    *, api_key: str, app_key: str, site: str
) -> IntegrationHealthResult:
    """Validate Datadog credentials with a monitor list request."""
    client = DatadogClient(DatadogConfig(api_key=api_key, app_key=app_key, site=site))
    result = client.list_monitors()
    if result.get("success"):
        return IntegrationHealthResult(
            ok=True,
            detail=f"Datadog validated against {site}; fetched {result.get('total', 0)} monitors.",
        )
    return IntegrationHealthResult(
        ok=False,
        detail=f"Datadog validation failed: {result.get('error', 'unknown error')}",
    )


def validate_honeycomb_integration(
    *,
    api_key: str,
    dataset: str,
    base_url: str,
) -> IntegrationHealthResult:
    """Validate Honeycomb credentials with auth and a lightweight query."""
    try:
        honeycomb_config = HoneycombIntegrationConfig.model_validate(
            {
                "api_key": api_key,
                "dataset": dataset,
                "base_url": base_url,
            }
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=str(err))

    client = HoneycombClient(honeycomb_config)
    auth_result = client.validate_access()
    if not auth_result.get("success"):
        return IntegrationHealthResult(
            ok=False,
            detail=f"Honeycomb auth failed: {auth_result.get('error', 'unknown error')}",
        )

    query_result = client.run_query(
        {"calculations": [{"op": "COUNT"}], "time_range": 900},
        limit=1,
    )
    if not query_result.get("success"):
        return IntegrationHealthResult(
            ok=False,
            detail=f"Honeycomb query failed: {query_result.get('error', 'unknown error')}",
        )

    return IntegrationHealthResult(
        ok=True,
        detail=(
            f"Honeycomb validated against dataset {honeycomb_config.dataset} "
            f"at {honeycomb_config.base_url}."
        ),
    )


def validate_coralogix_integration(
    *,
    api_key: str,
    base_url: str,
    application_name: str = "",
    subsystem_name: str = "",
) -> IntegrationHealthResult:
    """Validate Coralogix access with a lightweight DataPrime query."""
    try:
        coralogix_config = CoralogixIntegrationConfig.model_validate(
            {
                "api_key": api_key,
                "base_url": base_url,
                "application_name": application_name,
                "subsystem_name": subsystem_name,
            }
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=str(err))

    client = CoralogixClient(coralogix_config)
    result = client.validate_access()
    if not result.get("success"):
        return IntegrationHealthResult(
            ok=False,
            detail=f"Coralogix validation failed: {result.get('error', 'unknown error')}",
        )

    scope: list[str] = []
    if coralogix_config.application_name:
        scope.append(f"application {coralogix_config.application_name}")
    if coralogix_config.subsystem_name:
        scope.append(f"subsystem {coralogix_config.subsystem_name}")
    scope_suffix = f" ({', '.join(scope)})" if scope else ""
    return IntegrationHealthResult(
        ok=True,
        detail=(
            f"Coralogix validated against {coralogix_config.base_url}{scope_suffix}; "
            f"DataPrime returned {result.get('total', 0)} row(s)."
        ),
    )


def validate_splunk_integration(
    *,
    base_url: str,
    token: str,
    index: str = "main",
    verify_ssl: bool = True,
    ca_bundle: str = "",
) -> IntegrationHealthResult:
    """Validate Splunk credentials by calling the server info endpoint."""
    client = SplunkClient(
        SplunkConfig(
            base_url=base_url,
            token=token,
            index=index,
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
        )
    )
    result = client.validate_access()
    if result.get("success"):
        return IntegrationHealthResult(ok=True, detail=result.get("detail", "Splunk connected."))
    return IntegrationHealthResult(
        ok=False,
        detail=f"Splunk validation failed: {result.get('error', 'unknown error')}",
    )


def validate_tempo_integration(
    *,
    url: str,
    api_key: str = "",
    username: str = "",
    password: str = "",
    org_id: str = "",
) -> IntegrationHealthResult:
    """Validate Tempo connectivity via the tag-search endpoint."""
    try:
        config = build_tempo_config(
            {
                "url": url,
                "api_key": api_key,
                "username": username,
                "password": password,
                "org_id": org_id,
            }
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Tempo config invalid: {err}")
    result = validate_tempo_config(config)
    return IntegrationHealthResult(ok=result.ok, detail=result.detail)


def validate_opensearch_integration(
    *,
    url: str,
    api_key: str = "",
    username: str = "",
    password: str = "",
) -> IntegrationHealthResult:
    """Validate OpenSearch / Elasticsearch connectivity via GET /_cluster/health.

    Supports three authentication modes:
    - No authentication (security disabled clusters)
    - API key (native to Elasticsearch and some OpenSearch deployments)
    - HTTP Basic Auth (default for most self-hosted OpenSearch clusters)
    """
    if not url:
        return IntegrationHealthResult(ok=False, detail="OpenSearch URL is required.")
    config = ElasticsearchConfig(
        url=url,
        api_key=api_key or None,
        username=username or None,
        password=password or None,
    )
    client = ElasticsearchClient(config)
    result = client.get_cluster_health()
    if result.get("success"):
        cluster_name = result.get("cluster_name") or "unknown"
        cluster_status = result.get("status") or "unknown"
        node_count = result.get("number_of_nodes", 0)
        return IntegrationHealthResult(
            ok=True,
            detail=(
                f"Connected to OpenSearch cluster '{cluster_name}' "
                f"({cluster_status}, {node_count} node(s))."
            ),
        )
    return IntegrationHealthResult(
        ok=False,
        detail=f"OpenSearch validation failed: {result.get('error', 'unknown error')}",
    )
