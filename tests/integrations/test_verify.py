from __future__ import annotations

from typing import Any

import pytest

from app.integrations.verify import (
    _verify_aws,
    _verify_coralogix,
    _verify_datadog,
    _verify_github,
    _verify_grafana,
    _verify_honeycomb,
    _verify_sentry,
    _verify_snowflake,
    _verify_tracer,
    _verify_vercel,
    resolve_effective_integrations,
    verification_exit_code,
    verify_integrations,
)


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def test_resolve_effective_integrations_prefers_local_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "grafana-local",
                "service": "grafana",
                "status": "active",
                "credentials": {
                    "endpoint": "https://store.grafana.net",
                    "api_key": "store-token",
                },
            }
        ],
    )
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://env.grafana.net")
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "env-token")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T000/B000/test")
    monkeypatch.setenv("JWT_TOKEN", "env-jwt")

    effective = resolve_effective_integrations()

    assert effective["grafana"]["source"] == "local store"
    assert effective["grafana"]["config"]["endpoint"] == "https://store.grafana.net"
    assert effective["slack"]["source"] == "local env"
    assert effective["tracer"]["source"] == "local env"


def test_resolve_effective_integrations_includes_honeycomb_and_coralogix_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("HONEYCOMB_API_KEY", "hny_test")
    monkeypatch.setenv("HONEYCOMB_DATASET", "prod-api")
    monkeypatch.setenv("CORALOGIX_API_KEY", "cx_test")
    monkeypatch.setenv("CORALOGIX_APPLICATION_NAME", "payments")
    monkeypatch.setenv("CORALOGIX_SUBSYSTEM_NAME", "worker")

    effective = resolve_effective_integrations()

    assert effective["honeycomb"]["config"]["dataset"] == "prod-api"
    assert effective["coralogix"]["config"]["application_name"] == "payments"


def test_resolve_effective_integrations_skips_snowflake_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT_IDENTIFIER", "env-account")
    monkeypatch.delenv("SNOWFLAKE_TOKEN", raising=False)
    monkeypatch.setenv("SNOWFLAKE_USER", "service-user")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret")

    effective = resolve_effective_integrations()

    assert "snowflake" not in effective


def test_resolve_effective_integrations_keeps_incomplete_datadog_store_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "datadog-local",
                "service": "datadog",
                "status": "active",
                "credentials": {
                    "api_key": "",
                    "app_key": "",
                    "site": "datadoghq.com",
                },
            }
        ],
    )

    effective = resolve_effective_integrations()

    assert effective["datadog"]["source"] == "local store"
    assert effective["datadog"]["config"]["integration_id"] == "datadog-local"
    assert effective["datadog"]["config"]["api_key"] == ""


def test_verify_grafana_passes_with_supported_datasource(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_requests_get(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(
            [
                {"type": "loki", "uid": "logs", "name": "Logs"},
                {"type": "prometheus", "uid": "metrics", "name": "Metrics"},
            ]
        )

    monkeypatch.setattr(
        "app.integrations._verification_adapters.requests.get",
        _fake_requests_get,
    )

    result = _verify_grafana(
        "local env",
        {"endpoint": "https://example.grafana.net", "api_key": "token"},
    )

    assert result["status"] == "passed"
    assert "loki" in result["detail"]
    assert "prometheus" in result["detail"]


def test_verify_datadog_reports_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.probes import ProbeResult
    from app.services.datadog.client import DatadogClient

    monkeypatch.setattr(
        DatadogClient,
        "probe_access",
        lambda _self: ProbeResult.failed("HTTP 403: forbidden"),
    )

    result = _verify_datadog(
        "local env",
        {"api_key": "dd-api", "app_key": "dd-app", "site": "datadoghq.com"},
    )

    assert result["status"] == "failed"
    assert "403" in result["detail"]


def test_verify_datadog_accepts_integration_id() -> None:
    result = _verify_datadog(
        "local store",
        {
            "api_key": "",
            "app_key": "",
            "site": "datadoghq.com",
            "integration_id": "datadog-local",
        },
    )

    assert result["status"] == "missing"
    assert "Missing API key" in result["detail"]


def test_verify_snowflake_requires_token() -> None:
    result = _verify_snowflake(
        "local env",
        {
            "account_identifier": "xy12345.us-east-1",
            "user": "service-user",
            "password": "secret",
            "token": "",
        },
    )

    assert result["status"] == "missing"
    assert result["detail"] == "Missing token credentials."


def test_verify_honeycomb_uses_auth_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.probes import ProbeResult
    from app.services.honeycomb import HoneycombClient

    monkeypatch.setattr(
        HoneycombClient,
        "probe_access",
        lambda _self: ProbeResult.passed(
            "Connected to Honeycomb dataset prod-api.", dataset="prod-api"
        ),
    )

    result = _verify_honeycomb(
        "local env",
        {"api_key": "hny_test", "dataset": "prod-api", "base_url": "https://api.honeycomb.io"},
    )

    assert result["status"] == "passed"
    assert "prod-api" in result["detail"]


def test_verify_coralogix_reports_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.probes import ProbeResult
    from app.services.coralogix import CoralogixClient

    monkeypatch.setattr(
        CoralogixClient,
        "probe_access",
        lambda _self: ProbeResult.failed("HTTP 401: unauthorized"),
    )

    result = _verify_coralogix(
        "local env",
        {
            "api_key": "cx_test",
            "base_url": "https://api.coralogix.com",
            "application_name": "payments",
            "subsystem_name": "worker",
        },
    )

    assert result["status"] == "failed"
    assert "401" in result["detail"]


def test_verify_aws_assume_role_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BaseSTSClient:
        def assume_role(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["RoleArn"] == "arn:aws:iam::123456789012:role/TracerReadOnly"
            assert kwargs["ExternalId"] == "external-123"
            return {
                "Credentials": {
                    "AccessKeyId": "ASIA_TEST",
                    "SecretAccessKey": "secret",
                    "SessionToken": "session",
                }
            }

    class _AssumedSTSClient:
        def get_caller_identity(self) -> dict[str, str]:
            return {
                "Account": "123456789012",
                "Arn": "arn:aws:sts::123456789012:assumed-role/TracerReadOnly/TracerIntegrationVerify",
            }

    def _fake_boto3_client(service_name: str, **kwargs: Any) -> Any:
        assert service_name == "sts"
        if kwargs.get("aws_access_key_id"):
            return _AssumedSTSClient()
        return _BaseSTSClient()

    monkeypatch.setattr("app.integrations._verification_adapters.boto3.client", _fake_boto3_client)

    result = _verify_aws(
        "local store",
        {
            "role_arn": "arn:aws:iam::123456789012:role/TracerReadOnly",
            "external_id": "external-123",
            "region": "us-east-1",
        },
    )

    assert result["status"] == "passed"
    assert "assume-role" in result["detail"]
    assert "123456789012" in result["detail"]


def test_verify_tracer_passes_with_env_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeTracerClient:
        def __init__(self, base_url: str, org_id: str, jwt_token: str) -> None:
            assert base_url == "https://app.tracer.cloud"
            assert org_id == "org_123"
            assert jwt_token == "jwt-token"

        def get_all_integrations(self) -> list[dict[str, str]]:
            return [{"id": "int-1"}, {"id": "int-2"}]

    monkeypatch.setattr(
        "app.integrations._verification_adapters.extract_org_id_from_jwt",
        lambda _token: "org_123",
    )
    monkeypatch.setattr(
        "app.integrations._verification_adapters.TracerClient",
        _FakeTracerClient,
    )

    result = _verify_tracer(
        "local env",
        {"base_url": "https://app.tracer.cloud", "jwt_token": "jwt-token"},
    )

    assert result["status"] == "passed"
    assert "org_123" in result["detail"]
    assert "2 integrations" in result["detail"]


def test_verify_github_passes_with_valid_streamable_http_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.integrations._verification_adapters as _adapters

    monkeypatch.setattr(
        _adapters,
        "_verify_with_validation_result",
        lambda service, source, _config, **_kw: {
            "service": service,
            "source": source,
            "status": "passed",
            "detail": "GitHub MCP ok",
        },
    )

    result = _verify_github(
        "local env",
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
        },
    )

    assert result["status"] == "passed"
    assert result["service"] == "github"


def test_verify_sentry_passes_with_valid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.integrations._verification_adapters as _adapters

    monkeypatch.setattr(
        _adapters,
        "_verify_with_validation_result",
        lambda service, source, _config, **_kw: {
            "service": service,
            "source": source,
            "status": "passed",
            "detail": "Sentry ok",
        },
    )

    result = _verify_sentry(
        "local env",
        {
            "base_url": "https://sentry.io",
            "organization_slug": "demo-org",
            "auth_token": "sntrys_test",
            "project_slug": "payments",
        },
    )

    assert result["status"] == "passed"
    assert result["service"] == "sentry"


def test_verification_exit_code_requires_core_success() -> None:
    assert (
        verification_exit_code(
            [
                {
                    "service": "slack",
                    "source": "local env",
                    "status": "configured",
                    "detail": "Incoming webhook configured.",
                }
            ]
        )
        == 1
    )

    assert (
        verification_exit_code(
            [
                {
                    "service": "grafana",
                    "source": "local env",
                    "status": "passed",
                    "detail": "Connected.",
                },
                {
                    "service": "slack",
                    "source": "local env",
                    "status": "configured",
                    "detail": "Incoming webhook configured.",
                },
            ]
        )
        == 0
    )

    assert (
        verification_exit_code(
            [
                {
                    "service": "grafana",
                    "source": "local env",
                    "status": "passed",
                    "detail": "Connected.",
                },
                {
                    "service": "slack",
                    "source": "local env",
                    "status": "failed",
                    "detail": "Webhook post failed.",
                },
            ]
        )
        == 1
    )

    assert (
        verification_exit_code(
            [
                {
                    "service": "slack",
                    "source": "local env",
                    "status": "configured",
                    "detail": "Incoming webhook configured.",
                }
            ],
            requested_service="slack",
        )
        == 0
    )


def test_verify_vercel_passes_with_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.probes import ProbeResult
    from app.services.vercel.client import VercelClient

    monkeypatch.setattr(
        VercelClient,
        "probe_access",
        lambda _self: ProbeResult.passed(
            "Connected to Vercel API and listed 2 project(s).", total=2
        ),
    )

    result = _verify_vercel("local env", {"api_token": "tok_test", "team_id": ""})

    assert result["status"] == "passed"
    assert "2 project" in result["detail"]


def test_verify_vercel_fails_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.probes import ProbeResult
    from app.services.vercel.client import VercelClient

    monkeypatch.setattr(
        VercelClient,
        "probe_access",
        lambda _self: ProbeResult.failed("HTTP 401: unauthorized"),
    )

    result = _verify_vercel("local env", {"api_token": "bad_token", "team_id": ""})

    assert result["status"] == "failed"
    assert "401" in result["detail"]


def test_verify_vercel_missing_token() -> None:
    result = _verify_vercel("local env", {"api_token": "", "team_id": ""})
    assert result["status"] == "missing"


def test_verify_integrations_dispatches_to_vercel(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.probes import ProbeResult
    from app.services.vercel.client import VercelClient

    monkeypatch.setattr(
        VercelClient,
        "probe_access",
        lambda _self: ProbeResult.passed(
            "Connected to Vercel API and listed 0 project(s).", total=0
        ),
    )
    monkeypatch.setattr(
        "app.integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "vercel-1",
                "service": "vercel",
                "status": "active",
                "credentials": {"api_token": "tok_test", "team_id": ""},
            }
        ],
    )

    results = verify_integrations("vercel")

    assert len(results) == 1
    assert results[0]["service"] == "vercel"
    assert results[0]["status"] == "passed"


def test_resolve_effective_integrations_includes_vercel_from_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "vercel-store-1",
                "service": "vercel",
                "status": "active",
                "credentials": {"api_token": "tok_store", "team_id": "team_xyz"},
            }
        ],
    )

    effective = resolve_effective_integrations()

    vercel = effective.get("vercel")
    assert vercel is not None
    assert vercel["config"]["api_token"] == "tok_store"
    assert vercel["config"]["team_id"] == "team_xyz"
    assert vercel["source"] == "local store"


def test_resolve_effective_integrations_includes_vercel_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("VERCEL_API_TOKEN", "tok_env")
    monkeypatch.setenv("VERCEL_TEAM_ID", "team_env")

    effective = resolve_effective_integrations()

    vercel = effective.get("vercel")
    assert vercel is not None
    assert vercel["config"]["api_token"] == "tok_env"
    assert vercel["source"] == "local env"
