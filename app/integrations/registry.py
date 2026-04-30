"""Central registry for integration metadata and verification dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.integrations._verification_adapters import (
    VerifierFn,
    _verify_alertmanager,
    _verify_argocd,
    _verify_aws,
    _verify_azure,
    _verify_azure_sql,
    _verify_betterstack,
    _verify_bitbucket,
    _verify_clickhouse,
    _verify_coralogix,
    _verify_datadog,
    _verify_discord,
    _verify_github,
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
    _verify_sentry,
    _verify_slack_without_test,
    _verify_snowflake,
    _verify_splunk,
    _verify_telegram,
    _verify_tracer,
    _verify_vercel,
)


@dataclass(frozen=True)
class IntegrationSpec:
    """Canonical metadata for one integration service."""

    service: str
    aliases: tuple[str, ...] = ()
    family_members: tuple[str, ...] = ()
    classifier: Any | None = None
    env_loader: Any | None = None
    effective_resolver: Any | None = None
    verifier: VerifierFn | None = None
    direct_effective: bool = False
    skip_classification: bool = False
    core_verify: bool = False
    setup_order: int | None = None
    verify_order: int | None = None


INTEGRATION_SPECS: tuple[IntegrationSpec, ...] = (
    IntegrationSpec(
        service="grafana",
        family_members=("grafana_local",),
        verifier=_verify_grafana,
        direct_effective=True,
        core_verify=True,
        setup_order=5,
        verify_order=2,
    ),
    IntegrationSpec(
        service="aws",
        aliases=("eks", "amazon eks"),
        verifier=_verify_aws,
        direct_effective=True,
        core_verify=True,
        setup_order=1,
        verify_order=7,
    ),
    IntegrationSpec(
        service="datadog",
        verifier=_verify_datadog,
        direct_effective=True,
        core_verify=True,
        setup_order=4,
        verify_order=3,
    ),
    IntegrationSpec(
        service="honeycomb",
        verifier=_verify_honeycomb,
        direct_effective=True,
        core_verify=True,
        setup_order=6,
        verify_order=4,
    ),
    IntegrationSpec(
        service="coralogix",
        aliases=("carologix",),
        verifier=_verify_coralogix,
        direct_effective=True,
        core_verify=True,
        setup_order=3,
        verify_order=5,
    ),
    IntegrationSpec(
        service="github",
        aliases=("github_mcp",),
        verifier=_verify_github,
        direct_effective=True,
        setup_order=14,
        verify_order=10,
    ),
    IntegrationSpec(
        service="sentry",
        verifier=_verify_sentry,
        direct_effective=True,
        setup_order=16,
        verify_order=11,
    ),
    IntegrationSpec(
        service="gitlab",
        verifier=None,
        direct_effective=True,
        setup_order=15,
        verify_order=None,
    ),
    IntegrationSpec(
        service="mongodb",
        aliases=("mongo",),
        verifier=_verify_mongodb,
        direct_effective=True,
        setup_order=17,
        verify_order=12,
    ),
    IntegrationSpec(
        service="postgresql",
        aliases=("postgres",),
        verifier=_verify_postgresql,
        direct_effective=True,
        setup_order=19,
        verify_order=13,
    ),
    IntegrationSpec(
        service="mongodb_atlas",
        aliases=("atlas",),
        verifier=_verify_mongodb_atlas,
        direct_effective=True,
        setup_order=8,
        verify_order=15,
    ),
    IntegrationSpec(
        service="mariadb",
        verifier=_verify_mariadb,
        direct_effective=True,
        setup_order=7,
        verify_order=16,
    ),
    IntegrationSpec(
        service="rabbitmq",
        verifier=_verify_rabbitmq,
        direct_effective=True,
        verify_order=17,
    ),
    IntegrationSpec(
        service="betterstack",
        aliases=("better stack",),
        verifier=_verify_betterstack,
        direct_effective=True,
        setup_order=2,
        verify_order=18,
    ),
    IntegrationSpec(
        service="vercel",
        verifier=_verify_vercel,
        direct_effective=True,
        setup_order=13,
        verify_order=20,
    ),
    IntegrationSpec(
        service="opsgenie",
        verifier=_verify_opsgenie,
        direct_effective=True,
        verify_order=21,
    ),
    IntegrationSpec(
        service="jira",
        verifier=None,
        direct_effective=True,
        verify_order=None,
    ),
    IntegrationSpec(
        service="discord",
        verifier=_verify_discord,
        direct_effective=True,
        setup_order=18,
        verify_order=25,
    ),
    IntegrationSpec(
        service="telegram",
        verifier=_verify_telegram,
        direct_effective=True,
        verify_order=26,
    ),
    IntegrationSpec(
        service="openclaw",
        verifier=_verify_openclaw,
        direct_effective=True,
        verify_order=28,
    ),
    IntegrationSpec(
        service="mysql",
        verifier=_verify_mysql,
        direct_effective=True,
        setup_order=20,
        verify_order=27,
    ),
    IntegrationSpec(
        service="azure_sql",
        verifier=_verify_azure_sql,
        direct_effective=True,
        setup_order=21,
        verify_order=14,
    ),
    IntegrationSpec(service="bitbucket", verifier=_verify_bitbucket, verify_order=24),
    IntegrationSpec(
        service="snowflake",
        verifier=_verify_snowflake,
        direct_effective=True,
        verify_order=29,
    ),
    IntegrationSpec(
        service="azure",
        aliases=("azure monitor", "azure_monitor"),
        verifier=_verify_azure,
        direct_effective=True,
        verify_order=30,
    ),
    IntegrationSpec(
        service="openobserve",
        aliases=("open observe",),
        verifier=_verify_openobserve,
        direct_effective=True,
        verify_order=31,
    ),
    IntegrationSpec(
        service="opensearch",
        aliases=("open search",),
        verifier=_verify_opensearch,
        direct_effective=True,
        setup_order=10,
        verify_order=32,
    ),
    IntegrationSpec(
        service="alertmanager",
        verifier=_verify_alertmanager,
        direct_effective=True,
        setup_order=0,
        verify_order=0,
    ),
    IntegrationSpec(
        service="splunk",
        verifier=_verify_splunk,
        direct_effective=True,
        verify_order=33,
    ),
    IntegrationSpec(
        service="airflow",
        aliases=("apache airflow",),
        verifier=None,
        direct_effective=True,
        verify_order=None,
    ),
    IntegrationSpec(
        service="argocd",
        verifier=_verify_argocd,
        direct_effective=True,
        verify_order=1,
    ),
    IntegrationSpec(
        service="slack",
        verifier=_verify_slack_without_test,
        skip_classification=True,
        setup_order=9,
        verify_order=8,
    ),
    IntegrationSpec(
        service="tracer",
        verifier=_verify_tracer,
        setup_order=12,
        verify_order=9,
    ),
    IntegrationSpec(service="google_docs", verifier=_verify_google_docs, verify_order=19),
    IntegrationSpec(service="kafka", verifier=_verify_kafka, verify_order=22),
    IntegrationSpec(service="clickhouse", verifier=_verify_clickhouse, verify_order=23),
    IntegrationSpec(service="notion"),
    IntegrationSpec(service="prefect"),
    IntegrationSpec(service="posthog"),
    IntegrationSpec(service="trello"),
    IntegrationSpec(service="rds", setup_order=11),
)

INTEGRATION_SPECS_BY_SERVICE = {spec.service: spec for spec in INTEGRATION_SPECS}

SERVICE_KEY_MAP: dict[str, str] = {}
for _spec in INTEGRATION_SPECS:
    SERVICE_KEY_MAP[_spec.service] = _spec.service
    for _alias in _spec.aliases:
        SERVICE_KEY_MAP[_alias] = _spec.service

SKIP_CLASSIFIED_SERVICES = frozenset(
    spec.service for spec in INTEGRATION_SPECS if spec.skip_classification
)

SERVICE_FAMILY_MAP: dict[str, str] = {spec.service: spec.service for spec in INTEGRATION_SPECS}
for _spec in INTEGRATION_SPECS:
    for _member in _spec.family_members:
        SERVICE_FAMILY_MAP[_member] = _spec.service

DIRECT_CLASSIFIED_EFFECTIVE_SERVICES = tuple(
    spec.service for spec in INTEGRATION_SPECS if spec.direct_effective
)

SUPPORTED_VERIFY_SERVICES = tuple(
    spec.service
    for spec in sorted(
        (candidate for candidate in INTEGRATION_SPECS if candidate.verifier is not None),
        key=lambda candidate: (
            candidate.verify_order if candidate.verify_order is not None else 10_000
        ),
    )
)

SUPPORTED_SETUP_SERVICES = tuple(
    spec.service
    for spec in sorted(
        (candidate for candidate in INTEGRATION_SPECS if candidate.setup_order is not None),
        key=lambda candidate: (
            candidate.setup_order if candidate.setup_order is not None else 10_000
        ),
    )
)

CORE_VERIFY_SERVICES = frozenset(spec.service for spec in INTEGRATION_SPECS if spec.core_verify)


def family_key(service_key: str) -> str:
    """Return the family key used for multi-instance sibling buckets."""
    return SERVICE_FAMILY_MAP.get(service_key, service_key)


def service_key(service_name: str) -> str:
    """Normalize an incoming service label to its canonical registry key."""
    lowered = service_name.strip().lower()
    return SERVICE_KEY_MAP.get(lowered, lowered)
