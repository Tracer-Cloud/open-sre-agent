from __future__ import annotations

from app.integrations.registry import (
    DIRECT_CLASSIFIED_EFFECTIVE_SERVICES,
    INTEGRATION_SPECS,
    SKIP_CLASSIFIED_SERVICES,
    SUPPORTED_SETUP_SERVICES,
    SUPPORTED_VERIFY_SERVICES,
    family_key,
    service_key,
)
from app.integrations.verify import VERIFIER_REGISTRY


def test_registry_declares_each_service_once() -> None:
    services = [spec.service for spec in INTEGRATION_SPECS]
    assert len(services) == len(set(services))


def test_registry_supported_lists_are_derived_from_specs() -> None:
    expected_verify = tuple(
        spec.service
        for spec in sorted(
            (candidate for candidate in INTEGRATION_SPECS if candidate.verifier is not None),
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
    assert set(VERIFIER_REGISTRY) == set(SUPPORTED_VERIFY_SERVICES)


def test_registry_preserves_aliases_and_special_case_buckets() -> None:
    assert service_key("github_mcp") == "github"
    assert service_key("carologix") == "coralogix"
    assert service_key("open search") == "opensearch"
    assert family_key("grafana_local") == "grafana"
    assert family_key("grafana") == "grafana"
    assert "slack" in SKIP_CLASSIFIED_SERVICES
    assert "grafana" in DIRECT_CLASSIFIED_EFFECTIVE_SERVICES
    assert "bitbucket" not in DIRECT_CLASSIFIED_EFFECTIVE_SERVICES
