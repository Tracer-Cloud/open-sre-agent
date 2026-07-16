"""Central registry for integration metadata and verification dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IntegrationSpec:
    """Canonical metadata for one integration service."""

    service: str
    aliases: tuple[str, ...] = ()
    family_members: tuple[str, ...] = ()
    classifier: Any | None = None
    env_loader: Any | None = None
    effective_resolver: Any | None = None
    has_verifier: bool = False
    direct_effective: bool = False
    skip_classification: bool = False
    core_verify: bool = False
    setup_order: int | None = None
    verify_order: int | None = None


INTEGRATION_SPECS: tuple[IntegrationSpec, ...] = (
    IntegrationSpec(
        service="grafana",
        family_members=("grafana_local",),
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=5,
        verify_order=2,
    ),
    IntegrationSpec(
        service="aws",
        aliases=("eks", "amazon eks"),
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=1,
        verify_order=7,
    ),
    IntegrationSpec(
        service="datadog",
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=4,
        verify_order=3,
    ),
    IntegrationSpec(
        service="groundcover",
        aliases=("gc",),
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=35,
        verify_order=46,
    ),
    IntegrationSpec(
        service="honeycomb",
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=6,
        verify_order=4,
    ),
    IntegrationSpec(
        service="coralogix",
        aliases=("carologix",),
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=3,
        verify_order=5,
    ),
    IntegrationSpec(
        service="github",
        aliases=("github_mcp",),
        has_verifier=True,
        direct_effective=True,
        setup_order=14,
        verify_order=10,
    ),
    IntegrationSpec(
        service="sentry",
        has_verifier=True,
        direct_effective=True,
        setup_order=16,
        verify_order=11,
    ),
    IntegrationSpec(
        service="gitlab",
        has_verifier=True,
        direct_effective=True,
        setup_order=15,
        verify_order=52,
    ),
    IntegrationSpec(
        service="jenkins",
        has_verifier=True,
        direct_effective=True,
        setup_order=24,
        verify_order=36,
    ),
    IntegrationSpec(
        service="mongodb",
        aliases=("mongo",),
        has_verifier=True,
        direct_effective=True,
        setup_order=17,
        verify_order=12,
    ),
    IntegrationSpec(
        service="postgresql",
        aliases=("postgres",),
        has_verifier=True,
        direct_effective=True,
        setup_order=19,
        verify_order=13,
    ),
    IntegrationSpec(
        service="mongodb_atlas",
        aliases=("atlas",),
        has_verifier=True,
        direct_effective=True,
        setup_order=8,
        verify_order=15,
    ),
    IntegrationSpec(
        service="mariadb",
        has_verifier=True,
        direct_effective=True,
        setup_order=7,
        verify_order=16,
    ),
    IntegrationSpec(
        service="rabbitmq",
        aliases=("amqp",),
        has_verifier=True,
        direct_effective=True,
        verify_order=17,
    ),
    IntegrationSpec(
        service="dagster",
        has_verifier=True,
        direct_effective=True,
        setup_order=29,
        verify_order=40,
    ),
    IntegrationSpec(
        service="redis",
        aliases=("valkey",),
        has_verifier=True,
        direct_effective=True,
        setup_order=30,
        verify_order=41,
    ),
    IntegrationSpec(
        service="betterstack",
        aliases=("better stack",),
        has_verifier=True,
        direct_effective=True,
        setup_order=2,
        verify_order=18,
    ),
    IntegrationSpec(
        service="vercel",
        has_verifier=True,
        direct_effective=True,
        setup_order=13,
        verify_order=20,
    ),
    IntegrationSpec(
        service="opsgenie",
        has_verifier=True,
        direct_effective=True,
        verify_order=21,
    ),
    IntegrationSpec(
        service="incident_io",
        aliases=("incident.io", "incidentio"),
        has_verifier=True,
        direct_effective=True,
        setup_order=22,
        verify_order=22,
    ),
    IntegrationSpec(
        service="jira",
        has_verifier=True,
        direct_effective=True,
        verify_order=50,
    ),
    IntegrationSpec(
        service="discord",
        has_verifier=True,
        direct_effective=True,
        setup_order=18,
        verify_order=25,
    ),
    IntegrationSpec(
        service="telegram",
        has_verifier=True,
        direct_effective=True,
        setup_order=26,
        verify_order=26,
    ),
    IntegrationSpec(
        service="rocketchat",
        aliases=("rocket.chat", "rocket chat"),
        has_verifier=True,
        direct_effective=True,
        setup_order=41,
        verify_order=55,
    ),
    IntegrationSpec(
        service="whatsapp",
        has_verifier=True,
        direct_effective=True,
        setup_order=27,
        verify_order=27,
    ),
    IntegrationSpec(
        service="twilio",
        has_verifier=True,
        direct_effective=True,
        setup_order=20,
        verify_order=28,
    ),
    IntegrationSpec(
        service="openclaw",
        has_verifier=True,
        direct_effective=True,
        setup_order=12,
        verify_order=39,
    ),
    IntegrationSpec(
        service="posthog_mcp",
        aliases=("posthog mcp", "posthog-mcp"),
        has_verifier=True,
        direct_effective=True,
        setup_order=33,
        verify_order=44,
    ),
    IntegrationSpec(
        service="sentry_mcp",
        aliases=("sentry mcp", "sentry-mcp"),
        has_verifier=True,
        direct_effective=True,
        setup_order=34,
        verify_order=45,
    ),
    IntegrationSpec(
        service="x_mcp",
        aliases=("x mcp", "x-mcp", "twitter", "twitter_mcp", "twitter-mcp"),
        has_verifier=True,
        direct_effective=True,
        setup_order=39,
        verify_order=49,
    ),
    IntegrationSpec(
        service="mysql",
        has_verifier=True,
        direct_effective=True,
        setup_order=28,
        verify_order=38,
    ),
    IntegrationSpec(
        service="azure_sql",
        has_verifier=True,
        direct_effective=True,
        setup_order=21,
        verify_order=14,
    ),
    IntegrationSpec(service="bitbucket", has_verifier=True, verify_order=24),
    IntegrationSpec(
        service="snowflake",
        has_verifier=True,
        direct_effective=True,
        verify_order=29,
    ),
    IntegrationSpec(
        service="azure",
        aliases=("azure monitor", "azure_monitor"),
        has_verifier=True,
        direct_effective=True,
        verify_order=30,
    ),
    IntegrationSpec(
        service="openobserve",
        aliases=("open observe",),
        has_verifier=True,
        direct_effective=True,
        verify_order=31,
    ),
    IntegrationSpec(
        service="opensearch",
        aliases=("open search",),
        has_verifier=True,
        direct_effective=True,
        setup_order=10,
        verify_order=32,
    ),
    IntegrationSpec(
        service="alertmanager",
        has_verifier=True,
        direct_effective=True,
        setup_order=0,
        verify_order=0,
    ),
    IntegrationSpec(
        service="splunk",
        has_verifier=True,
        direct_effective=True,
        verify_order=33,
    ),
    IntegrationSpec(
        service="airflow",
        aliases=("apache airflow",),
        direct_effective=True,
        verify_order=None,
    ),
    IntegrationSpec(
        service="argocd",
        has_verifier=True,
        direct_effective=True,
        verify_order=1,
    ),
    IntegrationSpec(
        service="helm",
        has_verifier=True,
        direct_effective=True,
        setup_order=38,
        verify_order=34,
    ),
    IntegrationSpec(
        service="victoria_logs",
        aliases=("victorialogs",),
        has_verifier=True,
        direct_effective=True,
        verify_order=6,
    ),
    IntegrationSpec(
        service="slack",
        has_verifier=True,
        direct_effective=True,
        setup_order=9,
        verify_order=8,
    ),
    IntegrationSpec(
        service="smtp",
        has_verifier=True,
        direct_effective=True,
        setup_order=36,
        verify_order=47,
    ),
    IntegrationSpec(
        service="tracer",
        has_verifier=True,
        setup_order=25,
        verify_order=9,
    ),
    IntegrationSpec(service="google_docs", has_verifier=True, verify_order=19),
    IntegrationSpec(service="kafka", has_verifier=True, verify_order=37),
    IntegrationSpec(service="clickhouse", has_verifier=True, verify_order=23),
    IntegrationSpec(service="alicloud", direct_effective=True),
    IntegrationSpec(service="notion"),
    IntegrationSpec(service="prefect", has_verifier=True, verify_order=51),
    IntegrationSpec(
        service="posthog",
        has_verifier=True,
        direct_effective=True,
        setup_order=40,
        verify_order=54,
    ),
    IntegrationSpec(service="trello"),
    IntegrationSpec(service="rds", setup_order=11),
    IntegrationSpec(
        service="supabase",
        has_verifier=True,
        verify_order=99,
    ),
    IntegrationSpec(
        service="signoz",
        has_verifier=True,
        direct_effective=True,
        setup_order=23,
        verify_order=35,
    ),
    IntegrationSpec(
        service="tempo",
        has_verifier=True,
        direct_effective=True,
        setup_order=32,
        verify_order=43,
    ),
    IntegrationSpec(
        service="pagerduty",
        has_verifier=True,
        direct_effective=True,
        setup_order=31,
        verify_order=42,
    ),
    IntegrationSpec(
        service="temporal",
        has_verifier=True,
        direct_effective=True,
        setup_order=37,
        verify_order=48,
    ),
    IntegrationSpec(
        service="kubernetes",
        aliases=("k8s",),
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=51,
        verify_order=53,
    ),
)

INTEGRATION_SPECS_BY_SERVICE = {spec.service: spec for spec in INTEGRATION_SPECS}

SERVICE_KEY_MAP: dict[str, str] = {spec.service: spec.service for spec in INTEGRATION_SPECS}
for _spec in INTEGRATION_SPECS:
    for _alias in _spec.aliases:
        SERVICE_KEY_MAP[_alias] = _spec.service

SKIP_CLASSIFIED_SERVICES: frozenset[str] = frozenset(
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
        (candidate for candidate in INTEGRATION_SPECS if candidate.has_verifier),
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


# Wire the concrete resolver into the platform-level seam so callers in
# ``tools/`` can normalize service keys without importing from
# ``integrations/`` directly (T-4 layering audit, issue #3352, item 27).
# Kept at import time so any consumer that has already imported the
# ``integrations`` package (every CLI entry point does so during startup)
# sees the real mapping instead of the identity fallback.
def _install_family_key_resolver() -> None:
    from platform.common.service_families import register_family_key_resolver

    register_family_key_resolver(family_key)


_install_family_key_resolver()


def service_key(service_name: str) -> str:
    """Normalize an incoming service label to its canonical registry key."""
    lowered = service_name.strip().lower()
    return SERVICE_KEY_MAP.get(lowered, lowered)


# Aliases that apply only to the integration-management commands (setup, verify,
# show, remove). These intentionally diverge from `service_key` / `SERVICE_KEY_MAP`
# when a user-facing label should map to a different canonical handler. Like
# Sentry, bare ``posthog`` is the REST credentials integration and ``posthog_mcp``
# is the separate MCP flow — they are not aliased to each other.
MANAGEMENT_SERVICE_ALIASES: dict[str, str] = {}


def resolve_management_service(service_name: str) -> str:
    """Resolve a service token for the integration-management CLI commands.

    Layers management-only aliases on top of the global `service_key`
    normalization when a CLI label should map to a different canonical handler.
    """
    lowered = service_name.strip().lower()
    aliased = MANAGEMENT_SERVICE_ALIASES.get(lowered)
    if aliased is not None:
        return aliased
    return service_key(lowered)
