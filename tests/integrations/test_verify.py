from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def clean_slack_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove SLACK_* env vars so the Slack verifier's Socket Mode branch only
    activates when a test configures tokens explicitly."""
    for key in list(os.environ):
        if key.startswith("SLACK_"):
            monkeypatch.delenv(key, raising=False)
    yield


from integrations.aws.verifier import verify_aws as _verify_aws
from integrations.coralogix.verifier import verify_coralogix as _verify_coralogix
from integrations.datadog.verifier import verify_datadog as _verify_datadog
from integrations.github.verifier import verify_github as _verify_github
from integrations.grafana.verifier import verify_grafana as _verify_grafana
from integrations.honeycomb.verifier import verify_honeycomb as _verify_honeycomb
from integrations.sentry.verifier import verify_sentry as _verify_sentry
from integrations.snowflake.verifier import verify_snowflake as _verify_snowflake
from integrations.telegram.verifier import verify_telegram as _verify_telegram
from integrations.tracer.verifier import verify_tracer as _verify_tracer
from integrations.vercel.verifier import verify_vercel as _verify_vercel
from integrations.verify import (
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
        "integrations.catalog.load_integrations",
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
    monkeypatch.setattr("integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("HONEYCOMB_API_KEY", "hny_test")
    monkeypatch.setenv("HONEYCOMB_DATASET", "prod-api")
    monkeypatch.setenv("CORALOGIX_API_KEY", "cx_test")
    monkeypatch.setenv("CORALOGIX_APPLICATION_NAME", "payments")
    monkeypatch.setenv("CORALOGIX_SUBSYSTEM_NAME", "worker")

    effective = resolve_effective_integrations()

    assert effective["honeycomb"]["config"]["dataset"] == "prod-api"
    assert effective["coralogix"]["config"]["application_name"] == "payments"


def test_resolve_effective_integrations_includes_posthog_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("POSTHOG_PROJECT_ID", "123")
    monkeypatch.setenv("POSTHOG_PERSONAL_API_KEY", "phx_test")

    effective = resolve_effective_integrations()

    assert effective["posthog"]["config"]["project_id"] == "123"
    assert effective["posthog"]["config"]["personal_api_key"] == "phx_test"


def test_resolve_effective_integrations_skips_posthog_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.catalog.load_integrations", lambda: [])
    monkeypatch.delenv("POSTHOG_PROJECT_ID", raising=False)
    monkeypatch.delenv("POSTHOG_PERSONAL_API_KEY", raising=False)

    effective = resolve_effective_integrations()

    assert "posthog" not in effective


def test_resolve_effective_integrations_skips_snowflake_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.catalog.load_integrations", lambda: [])
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
        "integrations.catalog.load_integrations",
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


def test_resolve_effective_integrations_drops_unrecognised_keys_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Unknown catalog keys must not crash EffectiveIntegrations (extra=forbid)."""
    from integrations import _catalog_impl as catalog_impl

    monkeypatch.setattr("integrations.catalog.load_integrations", lambda: [])
    fake_service = "zzz_unknown_effective_key"
    orig_direct = catalog_impl.DIRECT_CLASSIFIED_EFFECTIVE_SERVICES
    monkeypatch.setattr(
        catalog_impl,
        "DIRECT_CLASSIFIED_EFFECTIVE_SERVICES",
        (*orig_direct, fake_service),
    )
    real_classify = catalog_impl.classify_integrations

    def classify_with_unknown(merged: list[dict[str, Any]]) -> dict[str, Any]:
        out = dict(real_classify(merged))
        out[fake_service] = {
            "id": "stub",
            "service": fake_service,
            "credentials": {},
        }
        return out

    monkeypatch.setattr(catalog_impl, "classify_integrations", classify_with_unknown)

    with caplog.at_level(logging.WARNING, logger="integrations._catalog_impl"):
        effective = resolve_effective_integrations()

    assert fake_service not in effective
    assert any("unrecognised integration key" in record.message for record in caplog.records), (
        caplog.text
    )
    assert fake_service in caplog.text


def test_verify_telegram_passes_with_get_me(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_requests_get(url: str, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        assert "getMe" in url
        return _FakeResponse({"ok": True, "result": {"username": "opensre_bot"}})

    monkeypatch.setattr(
        "integrations.telegram.verifier.requests.get",
        _fake_requests_get,
    )
    result = _verify_telegram(
        "local store",
        {"bot_token": "123:ABC", "default_chat_id": "-100123"},
    )
    assert result["status"] == "passed"
    assert "opensre_bot" in result["detail"]


def test_verify_telegram_missing_token() -> None:
    result = _verify_telegram("local env", {"bot_token": ""})
    assert result["status"] == "missing"
    assert "bot_token" in result["detail"].lower()


def test_verify_telegram_api_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.telegram.verifier.requests.get",
        lambda *_a, **_kw: _FakeResponse({"ok": False, "description": "Unauthorized"}),
    )
    result = _verify_telegram("local store", {"bot_token": "bad"})
    assert result["status"] == "failed"
    assert "unauthorized" in result["detail"].lower()


def test_verify_slack_send_test_posts_to_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: ``verify_integrations("slack", send_slack_test=True)`` must
    actually deliver the test message through the verifier's HTTP path.

    Protects the cross-module ``RUNTIME_SEND_TEST_KEY`` plumbing: ``verify.py``
    injects the key into config, ``verifiers/slack.py`` reads it, the
    ``httpx.post`` call fires. If either side drifts (rename, typo,
    silent fallthrough) this test fails — without it, ``--send-slack-test``
    could silently stop delivering.
    """
    webhook_url = "https://hooks.slack.com/services/T000/B000/test"
    monkeypatch.setattr(
        "integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "slack-local",
                "service": "slack",
                "status": "active",
                "instances": [
                    {
                        "name": "default",
                        "tags": {},
                        "credentials": {"webhook_url": webhook_url},
                    }
                ],
            }
        ],
    )
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    posted: list[tuple[str, dict[str, Any]]] = []

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

    def _fake_post(url: str, *_args: Any, json: dict[str, Any], **_kwargs: Any) -> _FakeResponse:
        posted.append((url, json))
        return _FakeResponse()

    monkeypatch.setattr("integrations.slack.verifier.httpx.post", _fake_post)

    results = verify_integrations("slack", send_slack_test=True)

    assert len(posted) == 1, "send_slack_test=True must trigger exactly one POST"
    posted_url, posted_payload = posted[0]
    assert posted_url == webhook_url
    assert "Tracer integration test" in posted_payload["text"]
    assert results == [
        {
            "service": "slack",
            "source": "local store",
            "status": "passed",
            "detail": "Webhook delivered test message successfully.",
        }
    ]


def test_verify_slack_send_test_false_does_not_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default (``send_slack_test=False``) must NOT POST to the webhook.

    Pins the inverse direction: a config-only slack verifier must remain
    side-effect-free unless the runtime flag is explicitly set.
    """
    monkeypatch.setattr(
        "integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "slack-local",
                "service": "slack",
                "status": "active",
                "instances": [
                    {
                        "name": "default",
                        "tags": {},
                        "credentials": {
                            "webhook_url": "https://hooks.slack.com/services/T000/B000/test"
                        },
                    }
                ],
            }
        ],
    )
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    def _fail_post(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("httpx.post must not be called when send_slack_test=False")

    monkeypatch.setattr("integrations.slack.verifier.httpx.post", _fail_post)

    results = verify_integrations("slack")  # default: send_slack_test=False

    assert results[0]["status"] == "passed"
    assert "Use --send-slack-test" in results[0]["detail"]


def test_verify_slack_mixed_webhook_and_socket_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """A config carrying webhook plus Socket Mode tokens must verify, not be
    rejected by the strict webhook-only model."""
    monkeypatch.setattr(
        "integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "slack-local",
                "service": "slack",
                "status": "active",
                "instances": [
                    {
                        "name": "default",
                        "tags": {},
                        "credentials": {
                            "webhook_url": "https://hooks.slack.com/services/T000/B000/test",
                            "bot_token": "xoxb-test",
                            "app_token": "xapp-test",
                        },
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(
        "integrations.slack.verifier.httpx.get",
        lambda *_args, **_kwargs: _FakeResponse({"ok": True, "team": "testspace"}),
    )

    results = verify_integrations("slack")

    assert results[0]["status"] == "passed"
    assert "Webhook configured." in results[0]["detail"]
    assert "auth.test ok" in results[0]["detail"]


def test_verify_slack_uses_v2_store_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "slack-local",
                "service": "slack",
                "status": "active",
                "instances": [
                    {
                        "name": "default",
                        "tags": {},
                        "credentials": {
                            "webhook_url": "https://hooks.slack.com/services/T000/B000/test"
                        },
                    }
                ],
            }
        ],
    )
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    results = verify_integrations("slack")

    assert results == [
        {
            "service": "slack",
            "source": "local store",
            "status": "passed",
            "detail": "Webhook configured. Use --send-slack-test to validate delivery.",
        }
    ]


def test_verify_grafana_passes_with_supported_datasource(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_requests_get(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(
            [
                {"type": "loki", "uid": "logs", "name": "Logs"},
                {"type": "prometheus", "uid": "metrics", "name": "Metrics"},
            ]
        )

    monkeypatch.setattr(
        "integrations.grafana.verifier.requests.get",
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
    from integrations.datadog.client import DatadogClient
    from integrations.probes import ProbeResult

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
    from integrations.honeycomb.client import HoneycombClient
    from integrations.probes import ProbeResult

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
    from integrations.coralogix.client import CoralogixClient
    from integrations.probes import ProbeResult

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

    monkeypatch.setattr("integrations.aws.verifier.boto3.client", _fake_boto3_client)

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
        "integrations.tracer.verifier.extract_org_id_from_jwt",
        lambda _token: "org_123",
    )
    monkeypatch.setattr(
        "integrations.tracer.verifier.TracerClient",
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
    from types import SimpleNamespace

    monkeypatch.setattr(
        f"{_verify_github.__module__}.validate_github_mcp_config",
        lambda _config: SimpleNamespace(ok=True, detail="GitHub MCP ok", failure_category=""),
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


def test_verify_github_reports_credential_less_store_record_as_missing() -> None:
    """A stale store record with no token is surfaced as missing, not a 401 failure."""

    verdict = _verify_github("local store", {})

    assert verdict["service"] == "github"
    assert verdict["status"] == "missing"
    assert "without an auth token" in verdict["detail"]


def test_verify_sentry_passes_with_valid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.verification.validation.verify_with_validation_result",
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
    from integrations.probes import ProbeResult
    from integrations.vercel.client import VercelClient

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
    from integrations.probes import ProbeResult
    from integrations.vercel.client import VercelClient

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
    from integrations.probes import ProbeResult
    from integrations.vercel.client import VercelClient

    monkeypatch.setattr(
        VercelClient,
        "probe_access",
        lambda _self: ProbeResult.passed(
            "Connected to Vercel API and listed 0 project(s).", total=0
        ),
    )
    monkeypatch.setattr(
        "integrations.catalog.load_integrations",
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
        "integrations.catalog.load_integrations",
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
    monkeypatch.setattr("integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("VERCEL_API_TOKEN", "tok_env")
    monkeypatch.setenv("VERCEL_TEAM_ID", "team_env")

    effective = resolve_effective_integrations()

    vercel = effective.get("vercel")
    assert vercel is not None
    assert vercel["config"]["api_token"] == "tok_env"
    assert vercel["source"] == "local env"


def test_resolve_effective_integrations_skips_invalid_slack_env_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-Slack SLACK_WEBHOOK_URL must not crash resolve_effective_integrations (Sentry #1987)."""
    monkeypatch.setattr("integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.com/not-slack")

    with caplog.at_level(logging.WARNING):
        effective = resolve_effective_integrations()

    assert "slack" not in effective
    assert any("SLACK_WEBHOOK_URL" in r.message for r in caplog.records)


def test_resolve_effective_integrations_skips_invalid_slack_store_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An invalid webhook_url in the store must not crash resolve_effective_integrations."""
    monkeypatch.setattr(
        "integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "slack-local",
                "service": "slack",
                "status": "active",
                "instances": [
                    {
                        "name": "default",
                        "tags": {},
                        "credentials": {"webhook_url": "https://example.com/not-slack"},
                    }
                ],
            }
        ],
    )
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    with caplog.at_level(logging.WARNING):
        effective = resolve_effective_integrations()

    assert "slack" not in effective
    assert any("Slack webhook" in r.message for r in caplog.records)
