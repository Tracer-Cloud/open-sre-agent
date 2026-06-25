from __future__ import annotations

from app.integrations._verifiers_loader import register_all_verifiers
from app.integrations.registry import (
    DIRECT_CLASSIFIED_EFFECTIVE_SERVICES,
    INTEGRATION_SPECS,
    SKIP_CLASSIFIED_SERVICES,
    SUPPORTED_SETUP_SERVICES,
    SUPPORTED_VERIFY_SERVICES,
    family_key,
    resolve_management_service,
    service_key,
)
from app.integrations.verification import list_verifiers

register_all_verifiers()


def test_registry_declares_each_service_once() -> None:
    services = [spec.service for spec in INTEGRATION_SPECS]
    assert len(services) == len(set(services))


def test_registry_supported_lists_are_derived_from_specs() -> None:
    expected_verify = tuple(
        spec.service
        for spec in sorted(
            (candidate for candidate in INTEGRATION_SPECS if candidate.has_verifier),
            key=lambda candidate: (
                candidate.verify_order if candidate.verify_order is not None else 10_000
            ),
        )
    )
    expected_setup = tuple(
        spec.service
        for spec in sorted(
            (candidate for candidate in INTEGRATION_SPECS if candidate.setup_order is not None),
            key=lambda candidate: (
                candidate.setup_order if candidate.setup_order is not None else 10_000
            ),
        )
    )

    assert expected_verify == SUPPORTED_VERIFY_SERVICES
    assert expected_setup == SUPPORTED_SETUP_SERVICES
    assert set(SUPPORTED_VERIFY_SERVICES).issubset(set(list_verifiers()))


def test_every_setup_spec_has_handler() -> None:
    # #2537: a spec with `setup_order` but no matching `_HANDLERS` entry lets
    # Click accept a service that cmd_setup cannot dispatch. Anchor the
    # inverse-drift here.
    from app.integrations.cli import _HANDLERS

    missing = [svc for svc in SUPPORTED_SETUP_SERVICES if svc not in _HANDLERS]
    assert not missing, (
        f"Registry declares setup_order for {missing} but no _HANDLERS entry "
        "in app/integrations/cli.py. These services are silently dropped from "
        "_SETUP_SERVICES, so `opensre integrations setup <svc>` will reject them "
        "with the 'Usage: setup <service>' error."
    )


def test_registry_preserves_aliases_and_special_case_buckets() -> None:
    assert service_key("github_mcp") == "github"
    assert service_key("carologix") == "coralogix"
    assert service_key("open search") == "opensearch"
    assert family_key("grafana_local") == "grafana"
    assert family_key("grafana") == "grafana"
    assert "slack" in SKIP_CLASSIFIED_SERVICES
    assert "grafana" in DIRECT_CLASSIFIED_EFFECTIVE_SERVICES
    assert "bitbucket" not in DIRECT_CLASSIFIED_EFFECTIVE_SERVICES


def test_resolve_management_service_maps_posthog_to_posthog_mcp() -> None:
    # The bare `posthog` integration has no interactive setup/verify flow, so
    # management commands should treat "posthog" as the PostHog MCP integration.
    assert resolve_management_service("posthog") == "posthog_mcp"
    assert resolve_management_service("  PostHog  ") == "posthog_mcp"
    assert resolve_management_service("posthog_mcp") == "posthog_mcp"
    # `posthog_mcp` must be a real setup + verify target for the alias to be useful.
    assert "posthog_mcp" in SUPPORTED_SETUP_SERVICES
    assert "posthog_mcp" in SUPPORTED_VERIFY_SERVICES


def test_resolve_management_service_leaves_other_services_unaliased() -> None:
    # Unrelated services pass through, and `sentry` must NOT collapse into the
    # separate `sentry_mcp` flow (unlike posthog, sentry has its own setup).
    assert resolve_management_service("datadog") == "datadog"
    assert resolve_management_service("sentry") == "sentry"
    assert resolve_management_service("sentry_mcp") == "sentry_mcp"
    # Global registry aliases still resolve through the management path.
    assert resolve_management_service("github_mcp") == "github"
