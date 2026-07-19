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


def test_env_services_banner_requires_all_env_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", "https://dev12345.service-now.com")
    monkeypatch.setenv("SERVICENOW_USERNAME", "admin")

    # URL + username alone must not read as configured: the verifier and the
    # env loader both require a password, and the banner must agree with them.
    assert "servicenow" not in load_env_integration_services()

    monkeypatch.setenv("SERVICENOW_PASSWORD", "s3cret")
    assert "servicenow" in load_env_integration_services()


def test_classify_servicenow_rejects_plain_http_for_remote_hosts() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "servicenow-http",
                "service": "servicenow",
                "status": "active",
                "credentials": {
                    "instance_url": "http://dev12345.service-now.com",
                    "username": "admin",
                    "password": "s3cret",
                },
            }
        ]
    )
    assert "servicenow" not in resolved


def test_classify_servicenow_allows_http_loopback() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "servicenow-local",
                "service": "servicenow",
                "status": "active",
                "credentials": {
                    "instance_url": "http://localhost:8080",
                    "username": "admin",
                    "password": "s3cret",
                },
            }
        ]
    )
    assert resolved["servicenow"].instance_url == "http://localhost:8080"


def test_classify_servicenow_falls_back_to_url_when_instance_url_blank() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "servicenow-blank",
                "service": "servicenow",
                "status": "active",
                "credentials": {
                    "instance_url": "   ",
                    "url": "https://dev9.service-now.com",
                    "username": "ops",
                    "password": "pw",
                },
            }
        ]
    )
    assert resolved["servicenow"].instance_url == "https://dev9.service-now.com"


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
    assert result["detail"] == "Configured for ServiceNow at https://dev12345.service-now.com."


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
