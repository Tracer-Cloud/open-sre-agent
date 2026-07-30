"""Authorization tests for the Fargate control-plane Lambda."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from platform.deployment_multi_tenant.lambda_control_plane.auth.iam_auth import (
    iam_principal_is_allowed,
)
from platform.deployment_multi_tenant.lambda_control_plane.utils.models import TenantApiCredential
from platform.deployment_multi_tenant.lambda_public_forwarder.authorizer import BearerAuthorizer


class _Credentials:
    def __init__(self, credential: TenantApiCredential | None) -> None:
        self.credential = credential
        self.lookups: list[str] = []

    def fetch_tenant_api_credential_by_key_id(self, key_id: str) -> TenantApiCredential | None:
        self.lookups.append(key_id)
        return self.credential


class _Secrets:
    def __init__(self, value: str, *, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.arns: list[str] = []

    def get_secret(self, secret_arn: str) -> str:
        self.arns.append(secret_arn)
        if self.fail:
            raise RuntimeError("provider detail must remain private")
        return self.value


def _credential(*, enabled: bool = True) -> TenantApiCredential:
    now = datetime.now(UTC)
    return TenantApiCredential(
        key_id="key_12345678",
        organization_id="org_123",
        secret_arn="arn:aws:secretsmanager:eu-west-1:123456789012:secret:tenant-api",
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


def test_bearer_authorizer_maps_key_metadata_and_compares_exact_secret() -> None:
    token = f"osre_key_12345678.{'a' * 32}"
    credentials = _Credentials(_credential())
    secrets = _Secrets(token)

    authorized = BearerAuthorizer(credentials, secrets).authorize(f"Bearer {token}")

    assert authorized is not None
    assert authorized.organization_id == "org_123"
    assert authorized.key_id == "key_12345678"
    assert credentials.lookups == ["key_12345678"]
    assert secrets.arns == [_credential().secret_arn]


def test_bearer_authorizer_rejects_bad_format_disabled_mismatch_and_provider_error() -> None:
    valid_token = f"osre_key_12345678.{'b' * 32}"

    malformed_credentials = _Credentials(_credential())
    assert (
        BearerAuthorizer(malformed_credentials, _Secrets(valid_token)).authorize("Bearer demo")
        is None
    )
    assert malformed_credentials.lookups == []

    assert (
        BearerAuthorizer(_Credentials(_credential(enabled=False)), _Secrets(valid_token)).authorize(
            f"Bearer {valid_token}"
        )
        is None
    )
    assert (
        BearerAuthorizer(_Credentials(_credential()), _Secrets("different")).authorize(
            f"Bearer {valid_token}"
        )
        is None
    )
    assert (
        BearerAuthorizer(
            _Credentials(_credential()),
            _Secrets(valid_token, fail=True),
        ).authorize(f"Bearer {valid_token}")
        is None
    )


def test_iam_principal_allowlist_accepts_role_session_and_rejects_missing_context() -> None:
    allowed = frozenset({"arn:aws:iam::123456789012:role/saas/control-plane"})
    event: dict[str, Any] = {
        "requestContext": {
            "authorizer": {
                "iam": {
                    "userArn": (
                        "arn:aws:sts::123456789012:assumed-role/control-plane/invocation-123"
                    )
                }
            }
        }
    }

    assert iam_principal_is_allowed(event, allowed)
    assert not iam_principal_is_allowed({}, allowed)
    assert not iam_principal_is_allowed(event, frozenset())
    assert not iam_principal_is_allowed(
        event,
        frozenset({"arn:aws:iam::123456789012:role/other"}),
    )
