"""HTTP proxy and authorizer tests for the control-plane and public-forwarder Lambdas."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from platform.deployment_multi_tenant.lambda_control_plane.handler import ControlPlaneApi
from platform.deployment_multi_tenant.lambda_control_plane.runtime import (
    LambdaApp as ControlPlaneApp,
)
from platform.deployment_multi_tenant.lambda_control_plane.utils.models import (
    AgentRun,
    AgentRunSource,
    AgentRunStatus,
    DeploymentActualState,
    DeploymentDesiredState,
    SizeProfile,
    TenantApiCredential,
    TenantDeployment,
)
from platform.deployment_multi_tenant.lambda_public_forwarder.authorizer import BearerAuthorizer
from platform.deployment_multi_tenant.lambda_public_forwarder.handler import PublicApiHandler
from platform.deployment_multi_tenant.lambda_public_forwarder.runtime import (
    LambdaApp as PublicForwarderApp,
)

_ALLOWED_ROLE = "arn:aws:iam::123456789012:role/saas-control-plane"
_TOKEN = f"osre_key_12345678.{'x' * 32}"


class _CredentialStore:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.credential = TenantApiCredential(
            key_id="key_12345678",
            organization_id="org_123",
            secret_arn="arn:aws:secretsmanager:eu-west-1:123:secret:tenant-api",
            enabled=True,
            created_at=now,
            updated_at=now,
        )

    def fetch_tenant_api_credential_by_key_id(self, key_id: str) -> TenantApiCredential | None:
        return self.credential if key_id == self.credential.key_id else None


class _SecretReader:
    def get_secret(self, _secret_arn: str) -> str:
        return _TOKEN


class _Runs:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []
        self.runs: dict[str, AgentRun] = {}

    def enqueue_agent_run(self, **kwargs: Any) -> AgentRun:
        self.enqueued.append(kwargs)
        now = datetime.now(UTC)
        run = AgentRun(
            id="run-123",
            organization_id=kwargs["organization_id"],
            source=kwargs["source"],
            prompt=kwargs["prompt"],
            source_event_id=kwargs["source_event_id"],
            status=AgentRunStatus.QUEUED,
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        self.runs[run.id] = run
        return run

    def fetch_agent_run_by_id(self, run_id: str) -> AgentRun | None:
        return self.runs.get(run_id)


@dataclass(frozen=True)
class _ProvisionResult:
    deployment: TenantDeployment
    api_credential: str | None


@dataclass(frozen=True)
class _RotateResult:
    key_id: str
    api_credential: str


class _Lifecycle:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.deployment = TenantDeployment(
            organization_id="org_123",
            desired_state=DeploymentDesiredState.RUNNING,
            actual_state=DeploymentActualState.RUNNING,
            size_profile=SizeProfile.SMALL,
            created_at=now,
            updated_at=now,
        )
        self.provision_calls: list[tuple[str, SizeProfile]] = []

    def provision_gateway(
        self,
        organization_id: str,
        size_profile: SizeProfile,
    ) -> _ProvisionResult:
        self.provision_calls.append((organization_id, size_profile))
        return _ProvisionResult(self.deployment, _TOKEN)

    def get_gateway(self, organization_id: str) -> TenantDeployment | None:
        return self.deployment if organization_id == "org_123" else None

    def start_gateway(self, _organization_id: str) -> TenantDeployment:
        return self.deployment

    def stop_gateway(self, _organization_id: str) -> TenantDeployment:
        return self.deployment

    def delete_gateway(self, _organization_id: str) -> TenantDeployment:
        return self.deployment

    def rotate_api_credential(self, _organization_id: str) -> _RotateResult:
        return _RotateResult("key_rotated1", _TOKEN)


def _control_plane_app(lifecycle: _Lifecycle | None = None) -> tuple[ControlPlaneApp, _Lifecycle]:
    resolved = lifecycle or _Lifecycle()
    return (
        ControlPlaneApp(
            control_plane=ControlPlaneApi(
                lifecycle=resolved,
                allowed_lifecycle_role_arns=frozenset({_ALLOWED_ROLE}),
            ),
        ),
        resolved,
    )


def _public_forwarder_app() -> tuple[PublicForwarderApp, _Runs]:
    runs = _Runs()
    credentials = _CredentialStore()
    return (
        PublicForwarderApp(
            public_api=PublicApiHandler(runs=runs),
            bearer_authorizer=BearerAuthorizer(credentials, _SecretReader()),
        ),
        runs,
    )


def _control_plane_api(lifecycle: _Lifecycle | None = None) -> ControlPlaneApi:
    return ControlPlaneApi(
        lifecycle=lifecycle or _Lifecycle(),
        allowed_lifecycle_role_arns=frozenset({_ALLOWED_ROLE}),
    )


def _event(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    iam: bool = False,
    tenant: str | None = None,
) -> dict[str, Any]:
    request_context: dict[str, Any] = {"http": {"method": method}}
    if iam:
        request_context["authorizer"] = {"iam": {"userArn": _ALLOWED_ROLE}}
    if tenant is not None:
        request_context["authorizer"] = {
            "lambda": {"organization_id": tenant, "key_id": "key_12345678"}
        }
    return {
        "version": "2.0",
        "rawPath": path,
        "requestContext": request_context,
        "body": json.dumps(body) if body is not None else None,
    }


def _payload(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


def test_authorizer_event_returns_tenant_context_only_for_valid_bearer() -> None:
    api, _ = _public_forwarder_app()
    event = {
        "type": "REQUEST",
        "routeArn": "arn:aws:execute-api:eu-west-1:123:api/$default/POST/v1/runs",
        "headers": {"Authorization": f"Bearer {_TOKEN}"},
    }

    assert api.handle(event) == {
        "isAuthorized": True,
        "context": {"organization_id": "org_123", "key_id": "key_12345678"},
    }
    event["headers"] = {"authorization": "Bearer invalid"}
    assert api.handle(event) == {"isAuthorized": False}


def test_lifecycle_requires_allowed_iam_principal_and_provisions_default_profile() -> None:
    api, lifecycle = _control_plane_app()
    path = "/v1/organizations/org_123/gateway"

    forbidden = api.handle(_event("PUT", path, body={}))
    assert forbidden["statusCode"] == 403

    response = api.handle(_event("PUT", path, body={}, iam=True))

    assert response["statusCode"] == 200
    assert lifecycle.provision_calls == [("org_123", SizeProfile.SMALL)]
    payload = _payload(response)
    assert payload["deployment"]["organization_id"] == "org_123"
    assert payload["api_credential"] == _TOKEN
    assert response["headers"]["cache-control"] == "no-store"


def test_lifecycle_routes_delegate_to_operation_methods() -> None:
    api = _control_plane_api()
    gateway_path = "/v1/organizations/org_123/gateway"

    for method, path in (
        ("GET", gateway_path),
        ("POST", f"{gateway_path}/start"),
        ("POST", f"{gateway_path}/stop"),
        ("DELETE", gateway_path),
    ):
        route_response = api.handle(_event(method, path, iam=True))
        assert route_response["statusCode"] == 200
        assert _payload(route_response)["deployment"]["organization_id"] == "org_123"

    rotate_response = api.handle(
        _event(
            "POST",
            "/v1/organizations/org_123/api-credential/rotate",
            iam=True,
        )
    )
    assert rotate_response["statusCode"] == 200
    assert _payload(rotate_response) == {
        "key_id": "key_rotated1",
        "api_credential": _TOKEN,
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/organizations/org_123/gateway"),
        ("GET", "/v1/organizations/org_123/gateway/start"),
        ("DELETE", "/v1/organizations/org_123/gateway/stop"),
        ("PUT", "/v1/organizations/org_123/api-credential/rotate"),
    ],
)
def test_lifecycle_routes_reject_unsupported_methods(method: str, path: str) -> None:
    response = _control_plane_api().handle(_event(method, path, iam=True))

    assert response["statusCode"] == 405
    assert _payload(response) == {"error": "method_not_allowed"}


def test_run_organization_comes_only_from_authorizer_context() -> None:
    api, runs = _public_forwarder_app()

    rejected = api.handle(
        _event(
            "POST",
            "/v1/runs",
            tenant="org_123",
            body={"prompt": "Investigate", "organization_id": "org_attacker"},
        )
    )
    assert rejected["statusCode"] == 400
    assert _payload(rejected) == {"error": "organization_not_allowed"}
    assert runs.enqueued == []

    accepted = api.handle(
        _event(
            "POST",
            "/v1/runs",
            tenant="org_123",
            body={"prompt": "Investigate", "source_event_id": "webhook-1"},
        )
    )
    assert accepted["statusCode"] == 202
    assert runs.enqueued == [
        {
            "organization_id": "org_123",
            "source": AgentRunSource.API,
            "prompt": "Investigate",
            "source_event_id": "webhook-1",
        }
    ]
    assert "organization_id" not in _payload(accepted)["run"]


def test_get_run_is_tenant_scoped_and_cross_tenant_returns_not_found() -> None:
    api, runs = _public_forwarder_app()
    created = api.handle(
        _event("POST", "/v1/runs", tenant="org_123", body={"prompt": "Investigate"})
    )
    assert created["statusCode"] == 202

    own = api.handle(_event("GET", "/v1/runs/run-123", tenant="org_123"))
    other = api.handle(_event("GET", "/v1/runs/run-123", tenant="org_other"))
    missing_auth = api.handle(_event("GET", "/v1/runs/run-123"))

    assert own["statusCode"] == 200
    assert other["statusCode"] == 404
    assert missing_auth["statusCode"] == 401
    assert runs.runs["run-123"].organization_id == "org_123"


def test_internal_failure_returns_generic_error_without_exception_detail() -> None:
    lifecycle = _Lifecycle()
    api = _control_plane_api(lifecycle)

    def fail(_organization_id: str) -> TenantDeployment:
        raise RuntimeError("secret provider response")

    lifecycle.start_gateway = fail  # type: ignore[method-assign]
    response = api.handle(
        _event(
            "POST",
            "/v1/organizations/org_123/gateway/start",
            iam=True,
        )
    )

    assert response["statusCode"] == 500
    assert _payload(response) == {"error": "internal_error"}
    assert "secret provider response" not in response["body"]
