from __future__ import annotations

import sys
import types
from importlib import import_module

import httpx
import pytest

from integrations.betterstack import BetterStackValidationResult
from integrations.github.mcp import GitHubMCPValidationResult
from surfaces.cli.wizard.integration_health import (
    validate_aws_integration,
    validate_betterstack_integration,
    validate_dagster_integration,
    validate_discord_bot,
    validate_github_mcp_integration,
    validate_grafana_integration,
    validate_incident_io_integration,
    validate_sentry_integration,
    validate_servicenow_integration,
    validate_slack_webhook,
    validate_vercel_integration,
)


def test_legacy_integration_health_import_surface_still_exports_validators() -> None:
    module = import_module("surfaces.cli.wizard.integration_health")

    expected_exports = {
        "IntegrationHealthResult",
        "validate_alertmanager_integration",
        "validate_aws_integration",
        "validate_betterstack_integration",
        "validate_dagster_integration",
        "validate_discord_bot",
        "validate_github_mcp_integration",
        "validate_gitlab_integration",
        "validate_google_docs_integration",
        "validate_grafana_integration",
        "validate_incident_io_integration",
        "validate_jenkins_integration",
        "validate_jira_integration",
        "validate_notion_integration",
        "validate_openclaw_integration",
        "validate_opensearch_integration",
        "validate_opsgenie_integration",
        "validate_posthog_integration",
        "validate_posthog_mcp_integration",
        "validate_pagerduty_integration",
        "validate_rocketchat",
        "validate_rocketchat_webhook",
        "validate_sentry_integration",
        "validate_sentry_mcp_integration",
        "validate_servicenow_integration",
        "validate_slack_webhook",
        "validate_splunk_integration",
        "validate_tempo_integration",
        "validate_vercel_integration",
    }

    assert set(module.__all__) == expected_exports
    for symbol in expected_exports:
        assert hasattr(module, symbol)


class _FakeGrafanaClient:
    def __init__(self, discovered: dict[str, str]) -> None:
        self._discovered = discovered

    def discover_datasource_uids(self) -> dict[str, str]:
        return self._discovered


def test_validate_grafana_integration_succeeds_when_datasources_are_discovered(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.observability.get_grafana_client_from_credentials",
        lambda **_kwargs: _FakeGrafanaClient({"loki_uid": "loki-1", "tempo_uid": "tempo-1"}),
    )

    result = validate_grafana_integration(endpoint="https://grafana.example.com", api_key="token")

    assert result.ok is True
    assert "datasource discovery" in result.detail


def test_validate_grafana_integration_fails_when_no_datasources_are_found(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.observability.get_grafana_client_from_credentials",
        lambda **_kwargs: _FakeGrafanaClient({}),
    )

    result = validate_grafana_integration(endpoint="https://grafana.example.com", api_key="token")

    assert result.ok is False
    assert "no datasources" in result.detail.lower()


def test_validate_incident_io_integration_succeeds(monkeypatch) -> None:
    class _FakeIncidentIoClient:
        def __init__(self, _config) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def list_incidents(self, **_kwargs):
            return {"success": True}

    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.alerting.IncidentIoClient",
        _FakeIncidentIoClient,
    )

    result = validate_incident_io_integration(api_key="iio_test")

    assert result.ok is True
    assert "api key accepted" in result.detail.lower()


@pytest.mark.parametrize("status_code", [200, 400, 403, 405])
def test_validate_slack_webhook_succeeds_for_allowed_probe_statuses(
    monkeypatch,
    status_code: int,
) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.http_probe_validators.httpx.get",
        lambda *_args, **_kwargs: types.SimpleNamespace(status_code=status_code),
    )

    result = validate_slack_webhook(webhook_url="https://hooks.slack.com/services/T000/B000/abc")

    assert result.ok is True
    assert "non-posting probe" in result.detail.lower()
    assert f"HTTP {status_code}" in result.detail


def test_validate_slack_webhook_fails_for_not_found(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.http_probe_validators.httpx.get",
        lambda *_args, **_kwargs: types.SimpleNamespace(status_code=404),
    )

    result = validate_slack_webhook(webhook_url="https://hooks.slack.com/services/T000/B000/abc")

    assert result.ok is False
    assert "404" in result.detail


def test_validate_slack_webhook_fails_for_httpx_request_error(monkeypatch) -> None:
    def _raise_request_error(*_args, **_kwargs):
        raise httpx.RequestError(
            "connection failed",
            request=httpx.Request("GET", "https://hooks.slack.com/services/T000/B000/abc"),
        )

    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.http_probe_validators.httpx.get",
        _raise_request_error,
    )

    result = validate_slack_webhook(webhook_url="https://hooks.slack.com/services/T000/B000/abc")

    assert result.ok is False
    assert "slack webhook validation failed" in result.detail.lower()
    assert "connection failed" in result.detail.lower()


def test_validate_servicenow_integration_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.http_probe_validators.httpx.get",
        lambda *_args, **_kwargs: types.SimpleNamespace(status_code=200),
    )

    result = validate_servicenow_integration(
        instance_url="https://dev12345.service-now.com/",
        username="admin",
        password="s3cret",
    )

    assert result.ok is True
    assert result.detail == "ServiceNow connected as admin at https://dev12345.service-now.com."


@pytest.mark.parametrize(
    ("status_code", "expected_fragment"),
    [
        (401, "credentials invalid"),
        (403, "cannot read the sys_user table"),
        (404, "instance URL not found"),
        (500, "unexpected status 500"),
    ],
)
def test_validate_servicenow_integration_maps_http_errors(
    monkeypatch,
    status_code: int,
    expected_fragment: str,
) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.http_probe_validators.httpx.get",
        lambda *_args, **_kwargs: types.SimpleNamespace(status_code=status_code),
    )

    result = validate_servicenow_integration(
        instance_url="https://dev12345.service-now.com",
        username="admin",
        password="bad",
    )

    assert result.ok is False
    assert expected_fragment in result.detail


def test_validate_servicenow_integration_rejects_plain_http_remote(monkeypatch) -> None:
    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("no request may be sent for a plaintext-HTTP remote URL")

    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.http_probe_validators.httpx.get",
        _fail_if_called,
    )

    result = validate_servicenow_integration(
        instance_url="http://dev12345.service-now.com",
        username="admin",
        password="s3cret",
    )

    assert result.ok is False
    assert "https://" in result.detail


def test_validate_servicenow_integration_fails_for_httpx_request_error(monkeypatch) -> None:
    def _raise_request_error(*_args, **_kwargs):
        raise httpx.RequestError(
            "connection failed",
            request=httpx.Request("GET", "https://dev12345.service-now.com/api/now/table/sys_user"),
        )

    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.http_probe_validators.httpx.get",
        _raise_request_error,
    )

    result = validate_servicenow_integration(
        instance_url="https://dev12345.service-now.com",
        username="admin",
        password="s3cret",
    )

    assert result.ok is False
    assert "servicenow validation failed" in result.detail.lower()
    assert "connection failed" in result.detail.lower()


def test_validate_slack_webhook_fails_for_invalid_host() -> None:
    result = validate_slack_webhook(webhook_url="https://example.com/services/T000/B000/abc")

    assert result.ok is False
    assert "slack domain" in result.detail.lower()


def test_validate_aws_integration_succeeds_with_static_credentials(monkeypatch) -> None:
    class _FakeSts:
        def get_caller_identity(self) -> dict[str, str]:
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/demo"}

    fake_boto3 = types.SimpleNamespace(client=lambda *_args, **_kwargs: _FakeSts())
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    result = validate_aws_integration(
        region="us-east-1",
        access_key_id="AKIA...",
        secret_access_key="secret",
    )

    assert result.ok is True
    assert "123456789012" in result.detail


def test_validate_aws_integration_succeeds_with_role_assumption(monkeypatch) -> None:
    class _FakeBaseSts:
        def assume_role(self, **_kwargs) -> dict[str, dict[str, str]]:
            return {
                "Credentials": {
                    "AccessKeyId": "ASIA...",
                    "SecretAccessKey": "secret",
                    "SessionToken": "token",
                }
            }

    class _FakeAssumedSts:
        def get_caller_identity(self) -> dict[str, str]:
            return {
                "Account": "123456789012",
                "Arn": "arn:aws:sts::123456789012:assumed-role/demo/session",
            }

    def _client(service_name: str, **kwargs):
        if service_name != "sts":
            raise AssertionError("unexpected service")
        if "aws_access_key_id" in kwargs:
            return _FakeAssumedSts()
        return _FakeBaseSts()

    fake_boto3 = types.SimpleNamespace(client=_client)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    result = validate_aws_integration(
        region="us-east-1",
        role_arn="arn:aws:iam::123456789012:role/demo",
        external_id="external-id",
    )

    assert result.ok is True
    assert "assumed-role" in result.detail


def test_validate_aws_integration_fails_when_boto3_client_raises(monkeypatch) -> None:
    class _FailingSts:
        def get_caller_identity(self) -> dict[str, str]:
            raise RuntimeError("denied")

    fake_boto3 = types.SimpleNamespace(client=lambda *_args, **_kwargs: _FailingSts())
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    result = validate_aws_integration(
        region="us-east-1",
        access_key_id="AKIA...",
        secret_access_key="secret",
        session_token="",
    )

    assert result.ok is False
    assert "denied" in result.detail.lower()


def test_validate_github_mcp_integration_uses_shared_validator(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.mcp_validators.validate_github_mcp_config",
        lambda _config, **_kwargs: GitHubMCPValidationResult(
            ok=True,
            detail="OK @ghuser; repos=1; owners=o; examples=o/r; mcp_tools=1",
            authenticated_user="ghuser",
            repo_access_count=1,
            repo_access_scope_owners=("o",),
            repo_access_samples=("o/r",),
        ),
    )

    result = validate_github_mcp_integration(
        url="https://api.githubcopilot.com/mcp/",
        mode="streamable-http",
        auth_token="ghp_test",
        toolsets=["repos"],
    )

    assert result.ok is True
    assert "Configuration validation: succeeded" in result.detail
    assert "GitHub identity: @ghuser" in result.detail
    assert "Repositories returned (probe): 1" in result.detail
    assert result.github_mcp is not None
    assert result.github_mcp.authenticated_user == "ghuser"


def test_validate_sentry_integration_uses_shared_validator(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.sentry.validate_sentry_config",
        lambda _config: types.SimpleNamespace(ok=True, detail="Sentry ok"),
    )

    result = validate_sentry_integration(
        base_url="https://sentry.io",
        organization_slug="demo-org",
        auth_token="sntrys_test",
        project_slug="payments",
    )

    assert result.ok is True
    assert result.detail == "Sentry ok"


def test_validate_dagster_integration_uses_shared_validator(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.dagster.validate_dagster_config",
        lambda _config: types.SimpleNamespace(
            ok=True, detail="Connected to Dagster version mock-1.0."
        ),
    )

    result = validate_dagster_integration(
        endpoint="http://localhost:3000/graphql",
        api_token="",
    )

    assert result.ok is True
    assert result.detail == "Connected to Dagster version mock-1.0."


def test_validate_dagster_integration_surfaces_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.dagster.validate_dagster_config",
        lambda _config: types.SimpleNamespace(
            ok=False, detail="Dagster GraphQL probe failed: HTTP 401"
        ),
    )

    result = validate_dagster_integration(
        endpoint="https://demo.dagster.cloud/graphql",
        api_token="bad",
    )

    assert result.ok is False
    assert "HTTP 401" in result.detail


class _FakeVercelClient:
    def __init__(self, result: dict) -> None:
        self._result = result

    def __enter__(self) -> _FakeVercelClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def list_projects(self) -> dict:
        return self._result


def test_validate_vercel_integration_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.vercel.VercelClient",
        lambda _config: _FakeVercelClient(
            {"success": True, "projects": [{"id": "p1"}], "total": 1}
        ),
    )

    result = validate_vercel_integration(api_token="tok_test")

    assert result.ok is True
    assert "1 project" in result.detail


def test_validate_vercel_integration_succeeds_with_team_id(monkeypatch) -> None:
    captured: dict = {}

    class _CapturingClient:
        def __init__(self, config) -> None:
            captured["team_id"] = config.team_id

        def __enter__(self) -> _CapturingClient:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def list_projects(self) -> dict:
            return {"success": True, "projects": [], "total": 0}

    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.vercel.VercelClient",
        _CapturingClient,
    )

    result = validate_vercel_integration(api_token="tok_test", team_id="team_xyz")

    assert result.ok is True
    assert captured["team_id"] == "team_xyz"


def test_validate_vercel_integration_fails_on_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.vercel.VercelClient",
        lambda _config: _FakeVercelClient({"success": False, "error": "HTTP 401: unauthorized"}),
    )

    result = validate_vercel_integration(api_token="bad_token")

    assert result.ok is False
    assert "401" in result.detail


def test_validate_vercel_integration_fails_with_empty_token() -> None:
    result = validate_vercel_integration(api_token="")

    assert result.ok is False
    assert "required" in result.detail.lower()


def test_validate_vercel_integration_surfaces_exception(monkeypatch) -> None:
    def _raise(_config):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.vercel.VercelClient",
        _raise,
    )

    result = validate_vercel_integration(api_token="tok_test")

    assert result.ok is False


# ---------------------------------------------------------------------------
# validate_discord_bot
# ---------------------------------------------------------------------------


def test_validate_discord_bot_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "httpx.get",
        lambda *_a, **_kw: types.SimpleNamespace(
            status_code=200,
            json=lambda: {"username": "my-sre-bot"},
        ),
    )
    result = validate_discord_bot(bot_token="Bot.valid.token")
    assert result.ok is True
    assert "my-sre-bot" in result.detail


def test_validate_discord_bot_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "httpx.get",
        lambda *_a, **_kw: types.SimpleNamespace(
            status_code=401,
            json=lambda: {"message": "401: Unauthorized"},
        ),
    )
    result = validate_discord_bot(bot_token="bad-token")
    assert result.ok is False
    assert "invalid or revoked" in result.detail.lower()


def test_validate_discord_bot_unexpected_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "httpx.get",
        lambda *_a, **_kw: types.SimpleNamespace(
            status_code=500,
            json=lambda: {},
        ),
    )
    result = validate_discord_bot(bot_token="some-token")
    assert result.ok is False
    assert "500" in result.detail


def test_validate_discord_bot_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx as _httpx

    def _raise(*_a: object, **_kw: object) -> None:
        raise _httpx.RequestError("connection refused")

    monkeypatch.setattr("httpx.get", _raise)
    result = validate_discord_bot(bot_token="some-token")
    assert result.ok is False
    assert "unreachable" in result.detail.lower()


def test_validate_betterstack_integration_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.alerting.validate_betterstack_config",
        lambda _config: BetterStackValidationResult(ok=True, detail="Connected."),
    )
    result = validate_betterstack_integration(
        query_endpoint="https://eu-nbg-2-connect.betterstackdata.com",
        username="u",
        password="p",
        sources=["t1_myapp"],
    )
    assert result.ok is True
    assert result.detail == "Connected."


def test_validate_betterstack_integration_forwards_failure_detail(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.wizard.integration_validators.alerting.validate_betterstack_config",
        lambda _config: BetterStackValidationResult(
            ok=False, detail="Better Stack authentication failed."
        ),
    )
    result = validate_betterstack_integration(
        query_endpoint="https://x",
        username="u",
        password="wrong",
    )
    assert result.ok is False
    assert "authentication" in result.detail.lower()


def test_validate_betterstack_integration_accepts_empty_tables() -> None:
    # Tables are optional; calling with no tables must not crash and must not
    # call network (covered by the probe-level tests separately).
    result = validate_betterstack_integration(
        query_endpoint="",
        username="",
        password="",
    )
    # Empty config returns the "required" detail from the underlying probe.
    assert result.ok is False
    assert "required" in result.detail.lower()
