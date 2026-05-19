"""Tenant credential onboarding API: store, health-check, and delete integration credentials."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
import jwt
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tenants", tags=["credentials"])

# ---------------------------------------------------------------------------
# Integration registry
# ---------------------------------------------------------------------------

INTEGRATION_KEYS: dict[str, list[str]] = {
    "datadog": ["DD_API_KEY", "DD_APP_KEY", "DD_SITE"],
    "grafana": ["GRAFANA_READ_TOKEN", "GRAFANA_INSTANCE_URL", "GRAFANA_LOKI_DATASOURCE_UID"],
    "kubernetes": ["KUBERNETES_KUBECONFIG"],
    "slack": ["SLACK_BOT_TOKEN"],
    "pagerduty": ["PAGERDUTY_API_KEY"],
    "aws": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"],
}

VAULT_PREFIX = os.environ.get("VAULT_PREFIX", "healops")
VAULT_REGION = (
    os.environ.get("VAULT_REGION")
    or os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
)

# ---------------------------------------------------------------------------
# Admin JWT auth dependency
# ---------------------------------------------------------------------------


def _get_admin_jwt_secret() -> str:
    secret = os.environ.get("ADMIN_JWT_SECRET", "")
    if not secret:
        raise HTTPException(status_code=500, detail="ADMIN_JWT_SECRET not configured")
    return secret


def _verify_admin_jwt(token: str) -> dict[str, Any]:
    """Verify HS256 admin JWT signed with ADMIN_JWT_SECRET."""
    secret = _get_admin_jwt_secret()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["sub", "exp", "role"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Admin token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid admin token: {exc}") from exc

    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Token does not have admin role")
    return payload


def require_admin(request: Request) -> dict[str, Any]:
    """FastAPI dependency: validates the admin JWT from the Authorization header."""
    raw = request.headers.get("Authorization", "")
    token = raw.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    return _verify_admin_jwt(token)


def _check_tenant_access(claims: dict[str, Any], tenant_id: str) -> None:
    """Return 403 if the JWT's sub/org doesn't match the requested tenant_id."""
    jwt_tenant = claims.get("tenant_id") or claims.get("org") or claims.get("sub", "")
    if jwt_tenant and jwt_tenant != tenant_id:
        raise HTTPException(status_code=403, detail="Token tenant_id does not match URL")


# ---------------------------------------------------------------------------
# Secrets Manager helpers
# ---------------------------------------------------------------------------


def _sm_client() -> Any:
    import boto3  # type: ignore[import-untyped]

    return boto3.client("secretsmanager", region_name=VAULT_REGION)


def _secret_name(tenant_id: str, key: str) -> str:
    return f"{VAULT_PREFIX}/{tenant_id}/{key}"


def _put_secret(client: Any, tenant_id: str, key: str, value: str) -> None:
    name = _secret_name(tenant_id, key)
    try:
        client.create_secret(Name=name, SecretString=value)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("ResourceExistsException", "ResourceNotFoundException"):
            client.put_secret_value(SecretId=name, SecretString=value)
        else:
            raise


def _get_secret(client: Any, tenant_id: str, key: str) -> str | None:
    name = _secret_name(tenant_id, key)
    try:
        return str(client.get_secret_value(SecretId=name)["SecretString"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise


def _delete_secret(client: Any, tenant_id: str, key: str) -> None:
    name = _secret_name(tenant_id, key)
    try:
        client.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            return  # already gone
        raise


# ---------------------------------------------------------------------------
# Integration health ping functions
# ---------------------------------------------------------------------------


def _ping_datadog(creds: dict[str, str]) -> str:
    api_key = creds.get("DD_API_KEY", "")
    app_key = creds.get("DD_APP_KEY", "")
    site = creds.get("DD_SITE", "datadoghq.com") or "datadoghq.com"
    if not api_key or not app_key:
        return "auth_error"
    try:
        resp = httpx.get(
            f"https://api.{site}/api/v1/monitor",
            headers={"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key},
            params={"page_size": 1},
            timeout=10,
        )
        if resp.status_code == 403:
            return "auth_error"
        resp.raise_for_status()
        return "ok"
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return "auth_error"
        logger.warning("Datadog ping failed: %s", exc)
        return "auth_error"
    except Exception as exc:
        logger.warning("Datadog ping error: %s", exc)
        return "auth_error"


def _ping_grafana(creds: dict[str, str]) -> str:
    token = creds.get("GRAFANA_READ_TOKEN", "")
    url = creds.get("GRAFANA_INSTANCE_URL", "")
    if not token or not url:
        return "auth_error"
    try:
        resp = httpx.get(
            f"{url.rstrip('/')}/api/datasources",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code in (401, 403):
            return "auth_error"
        resp.raise_for_status()
        return "ok"
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return "auth_error"
        logger.warning("Grafana ping failed: %s", exc)
        return "auth_error"
    except Exception as exc:
        logger.warning("Grafana ping error: %s", exc)
        return "auth_error"


def _ping_kubernetes(creds: dict[str, str]) -> str:
    kubeconfig = creds.get("KUBERNETES_KUBECONFIG", "")
    if not kubeconfig:
        return "auth_error"
    try:
        import base64
        import tempfile

        from kubernetes import client as k8s_client  # type: ignore[import-untyped]
        from kubernetes import config as k8s_config  # type: ignore[import-untyped]

        decoded = base64.b64decode(kubeconfig).decode()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=True) as f:
            f.write(decoded)
            f.flush()
            k8s_config.load_kube_config(config_file=f.name)

        v1 = k8s_client.CoreV1Api()
        v1.list_namespace(_request_timeout=10)
        return "ok"
    except Exception as exc:
        logger.warning("Kubernetes ping error: %s", exc)
        return "auth_error"


def _ping_slack(creds: dict[str, str]) -> str:
    token = creds.get("SLACK_BOT_TOKEN", "")
    if not token:
        return "auth_error"
    try:
        resp = httpx.post(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            return "auth_error"
        return "ok"
    except Exception as exc:
        logger.warning("Slack ping error: %s", exc)
        return "auth_error"


def _ping_pagerduty(creds: dict[str, str]) -> str:
    api_key = creds.get("PAGERDUTY_API_KEY", "")
    if not api_key:
        return "auth_error"
    try:
        resp = httpx.get(
            "https://api.pagerduty.com/abilities",
            headers={"Authorization": f"Token token={api_key}", "Accept": "application/vnd.pagerduty+json;version=2"},
            timeout=10,
        )
        if resp.status_code in (401, 403):
            return "auth_error"
        resp.raise_for_status()
        return "ok"
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return "auth_error"
        logger.warning("PagerDuty ping failed: %s", exc)
        return "auth_error"
    except Exception as exc:
        logger.warning("PagerDuty ping error: %s", exc)
        return "auth_error"


def _ping_aws(creds: dict[str, str]) -> str:
    access_key = creds.get("AWS_ACCESS_KEY_ID", "")
    secret_key = creds.get("AWS_SECRET_ACCESS_KEY", "")
    region = creds.get("AWS_REGION", "us-east-1") or "us-east-1"
    if not access_key or not secret_key:
        return "auth_error"
    try:
        import boto3  # type: ignore[import-untyped]

        sts = boto3.client(
            "sts",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        sts.get_caller_identity()
        return "ok"
    except Exception as exc:
        logger.warning("AWS ping error: %s", exc)
        return "auth_error"


_PING_FUNCTIONS = {
    "datadog": _ping_datadog,
    "grafana": _ping_grafana,
    "kubernetes": _ping_kubernetes,
    "slack": _ping_slack,
    "pagerduty": _ping_pagerduty,
    "aws": _ping_aws,
}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class StoreCredentialsRequest(BaseModel):
    integration: str
    credentials: dict[str, str]


class StoreCredentialsResponse(BaseModel):
    integration: str
    keys_stored: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/{tenant_id}/credentials",
    response_model=StoreCredentialsResponse,
    status_code=201,
)
def store_credentials(
    tenant_id: str,
    body: StoreCredentialsRequest,
    claims: dict[str, Any] = Depends(require_admin),
) -> StoreCredentialsResponse:
    """Store integration credentials for a tenant in Secrets Manager."""
    _check_tenant_access(claims, tenant_id)

    integration = body.integration.lower()
    if integration not in INTEGRATION_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown integration {body.integration!r}. Valid integrations: {sorted(INTEGRATION_KEYS)}",
        )

    allowed_keys = set(INTEGRATION_KEYS[integration])
    invalid = set(body.credentials) - allowed_keys
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unexpected keys for {integration}: {sorted(invalid)}. Expected: {sorted(allowed_keys)}",
        )

    client = _sm_client()
    stored: list[str] = []
    for key, value in body.credentials.items():
        _put_secret(client, tenant_id, key, value)
        stored.append(key)

    return StoreCredentialsResponse(integration=integration, keys_stored=stored)


@router.get("/{tenant_id}/credentials/health")
def health_check(
    tenant_id: str,
    claims: dict[str, Any] = Depends(require_admin),
) -> dict[str, str]:
    """Check health of all configured integrations for a tenant."""
    _check_tenant_access(claims, tenant_id)

    client = _sm_client()
    result: dict[str, str] = {}

    for integration, keys in INTEGRATION_KEYS.items():
        creds: dict[str, str] = {}
        for key in keys:
            val = _get_secret(client, tenant_id, key)
            if val is not None:
                creds[key] = val

        if not creds:
            result[integration] = "missing"
            continue

        ping_fn = _PING_FUNCTIONS.get(integration)
        if ping_fn is None:
            result[integration] = "missing"
            continue

        result[integration] = ping_fn(creds)

    return result


@router.delete("/{tenant_id}/credentials/{integration}", status_code=204)
def delete_credentials(
    tenant_id: str,
    integration: str,
    claims: dict[str, Any] = Depends(require_admin),
) -> Response:
    """Remove all secrets for an integration from Secrets Manager."""
    _check_tenant_access(claims, tenant_id)

    integration = integration.lower()
    if integration not in INTEGRATION_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown integration {integration!r}. Valid integrations: {sorted(INTEGRATION_KEYS)}",
        )

    client = _sm_client()
    for key in INTEGRATION_KEYS[integration]:
        _delete_secret(client, tenant_id, key)

    return Response(status_code=204)
