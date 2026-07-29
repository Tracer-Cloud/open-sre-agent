"""Data models shared by multi-tenant deployment and Gateway runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class SizeProfile(StrEnum):
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"


class DeploymentDesiredState(StrEnum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    DELETED = "DELETED"


class DeploymentActualState(StrEnum):
    PROVISIONING = "PROVISIONING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    DELETED = "DELETED"
    FAILED = "FAILED"


class AgentRunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AgentRunSource(StrEnum):
    API = "API"
    SLACK = "SLACK"
    TELEGRAM = "TELEGRAM"
    SCHEDULER = "SCHEDULER"


@dataclass(frozen=True, slots=True)
class TenantDeployment:
    organization_id: str
    desired_state: DeploymentDesiredState
    actual_state: DeploymentActualState
    size_profile: SizeProfile
    created_at: datetime
    updated_at: datetime
    cluster_arn: str | None = None
    service_arn: str | None = None
    task_definition_arn: str | None = None
    task_role_arn: str | None = None
    s3_filesystem_arn: str | None = None
    s3_access_point_arn: str | None = None
    bootstrap_secret_arn: str | None = None
    last_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRun:
    id: str
    organization_id: str
    source: AgentRunSource
    prompt: str
    status: AgentRunStatus
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    source_event_id: str | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    claimed_by: str | None = None
    lease_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TenantApiCredential:
    """Public credential metadata; the bearer secret is never persisted here."""

    key_id: str
    organization_id: str
    secret_arn: str
    enabled: bool
    created_at: datetime
    updated_at: datetime
    rotated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProvisionGatewayResult:
    deployment: TenantDeployment
    api_credential: str | None


@dataclass(frozen=True, slots=True)
class RotatedApiCredential:
    key_id: str
    api_credential: str
