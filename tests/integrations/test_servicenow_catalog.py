"""Catalog, env loading, and verification coverage for ServiceNow."""

from __future__ import annotations

import pytest

from integrations.catalog import (
    classify_integrations,
    load_env_integration_services,
    resolve_effective_integrations,
)
from integrations.config_models import ServiceNowIntegrationConfig
from integrations.servicenow.verifier import verify_servicenow as _verify_servicenow
from integrations.verify import verify_integrations


@pytest.fixture(autouse=True)
def _clear_servicenow_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SERVICENOW_INSTANCE_URL",
        "SERVICENOW_USERNAME",
        "SERVICENOW_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_classify_servicenow_store_record() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "servicenow-store-1",
                "service": "servicenow",
                "status": "active",
                "credentials": {
                    "instance_url": "https://dev12345.service-now.com",
                    "username": "admin",
                    "password": "s3cret",
                },
            }
        ]
    )

    cfg = resolved["servicenow"]
    assert cfg.instance_url == "https://dev12345.service-now.com"
    assert cfg.username == "admin"
    assert cfg.password == "s3cret"
    assert cfg.integration_id == "servicenow-store-1"
    assert cfg.auth == ("admin", "s3cret")
    assert cfg.api_base == "https://dev12345.service-now.com/api/now"


def test_classify_servicenow_accepts_url_credential_key() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "servicenow-alt",
                "service": "servicenow",
                "status": "active",
                "credentials": {
                    "url": "https://dev9.service-now.com",
                    "username": "ops",
                    "password": "pw",
                },
            }
        ]
    )
    assert resolved["servicenow"].instance_url == "https://dev9.service-now.com"


def test_classify_servicenow_rejects_missing_credentials() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "servicenow-partial",
                "service": "servicenow",
                "status": "active",
                "credentials": {"instance_url": "https://dev12345.service-now.com"},
            }
        ]
    )
    assert "servicenow" not in resolved


def test_servicenow_config_normalizes_values() -> None:
    cfg = ServiceNowIntegrationConfig(
        instance_url=" https://dev12345.service-now.com/ ",
        username=" admin ",
        password=" s3cret ",
        integration_id=" x ",
    )
    assert cfg.instance_url == "https://dev12345.service-now.com"
    assert cfg.username == "admin"
    assert cfg.password == "s3cret"
    assert cfg.integration_id == "x"


def test_resolve_effective_integrations_includes_servicenow_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", "https://dev12345.service-now.com")
    monkeypatch.setenv("SERVICENOW_USERNAME", "admin")
    monkeypatch.setenv("SERVICENOW_PASSWORD", "s3cret")

    effective = resolve_effective_integrations()
    servicenow = effective.get("servicenow")
    assert servicenow is not None
    assert servicenow["source"] == "local env"
    assert servicenow["config"]["instance_url"] == "https://dev12345.service-now.com"
    assert servicenow["config"]["username"] == "admin"


def test_env_loader_skips_servicenow_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", "https://dev12345.service-now.com")

    effective = resolve_effective_integrations()
    assert effective.get("servicenow") is None


def test_env_services_banner_lists_servicenow_without_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", "https://dev12345.service-now.com")
    monkeypatch.setenv("SERVICENOW_USERNAME", "admin")

    assert "servicenow" in load_env_integration_services()


def test_verify_servicenow_passes_with_full_config() -> None:
    result = _verify_servicenow(
        "local env",
        {
            "instance_url": "https://dev12345.service-now.com",
            "username": "admin",
            "password": "s3cret",
        },
    )
    assert result["status"] == "passed"
    assert "dev12345.service-now.com" in result["detail"]


def test_verify_servicenow_missing_without_credentials() -> None:
    result = _verify_servicenow(
        "local env",
        {"instance_url": "https://dev12345.service-now.com", "username": "", "password": ""},
    )
    assert result["status"] == "missing"


def test_verify_integrations_dispatches_to_servicenow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "servicenow-1",
                "service": "servicenow",
                "status": "active",
                "credentials": {
                    "instance_url": "https://dev12345.service-now.com",
                    "username": "admin",
                    "password": "s3cret",
                },
            }
        ],
    )

    results = verify_integrations("servicenow")
    assert len(results) == 1
    assert results[0]["service"] == "servicenow"
    assert results[0]["status"] == "passed"
