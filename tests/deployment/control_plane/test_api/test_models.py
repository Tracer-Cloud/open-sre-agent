"""Contract-level tests for the Fargate control-plane records."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

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


def test_deployment_record_contains_only_non_secret_resource_metadata() -> None:
    now = datetime.now(UTC)
    deployment = TenantDeployment(
        organization_id="org_123",
        desired_state=DeploymentDesiredState.RUNNING,
        actual_state=DeploymentActualState.PROVISIONING,
        size_profile=SizeProfile.SMALL,
        created_at=now,
        updated_at=now,
        bootstrap_secret_arn="arn:aws:secretsmanager:eu-west-1:123:secret:bootstrap",
    )

    assert deployment.organization_id == "org_123"
    assert "credential" not in deployment.__slots__
    assert "secret_value" not in deployment.__slots__
    with pytest.raises(FrozenInstanceError):
        deployment.actual_state = DeploymentActualState.RUNNING  # type: ignore[misc]


def test_agent_run_record_tracks_lease_and_deduplication_identity() -> None:
    now = datetime.now(UTC)
    run = AgentRun(
        id="run_1",
        organization_id="org_123",
        source=AgentRunSource.API,
        source_event_id="request_123",
        prompt="Investigate latency",
        status=AgentRunStatus.RUNNING,
        claimed_by="worker_1",
        lease_expires_at=now,
        attempt_count=2,
        created_at=now,
        updated_at=now,
    )

    assert run.source_event_id == "request_123"
    assert run.claimed_by == "worker_1"
    assert run.attempt_count == 2


def test_api_credential_record_has_reference_but_no_plaintext_secret_field() -> None:
    now = datetime.now(UTC)
    credential = TenantApiCredential(
        key_id="key_123",
        organization_id="org_123",
        secret_arn="arn:aws:secretsmanager:eu-west-1:123:secret:public-api",
        enabled=True,
        created_at=now,
        updated_at=now,
    )

    assert credential.key_id == "key_123"
    assert "secret_arn" in credential.__slots__
    assert "secret" not in credential.__slots__
    assert "token" not in credential.__slots__
