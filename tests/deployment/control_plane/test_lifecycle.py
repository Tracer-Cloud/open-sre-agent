"""Focused tests for idempotent tenant Gateway lifecycle reconciliation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from platform.deployment_multi_tenant.lambda_control_plane.aws.ecs import (
    FargateServiceState,
    TenantEcsAdapter,
)
from platform.deployment_multi_tenant.lambda_control_plane.aws.iam import (
    TenantIamAdapter,
    TenantMountBinding,
)
from platform.deployment_multi_tenant.lambda_control_plane.aws.s3_files import (
    S3FilesAccessPoint,
    S3FilesAdapter,
)
from platform.deployment_multi_tenant.lambda_control_plane.aws.secrets_manager import (
    TenantPublicApiCredential,
    TenantSecretsManagerAdapter,
)
from platform.deployment_multi_tenant.lambda_control_plane.methods.fargate_container_service import (
    FargateContainerLifecycle,
)
from platform.deployment_multi_tenant.lambda_control_plane.methods.lifecycle_errors import (
    LifecycleCapacityError,
    LifecycleOperationError,
    LifecycleValidationError,
)
from platform.deployment_multi_tenant.lambda_control_plane.utils.get_fleet_config import (
    TenantFleetConfig,
)
from platform.deployment_multi_tenant.lambda_control_plane.utils.models import (
    DeploymentActualState,
    DeploymentDesiredState,
    SizeProfile,
    TenantApiCredential,
    TenantDeployment,
)

NOW = datetime(2026, 7, 24, 12, tzinfo=UTC)
FILE_SYSTEM_ARN = "arn:aws:s3files:eu-west-2:123456789012:file-system/fs-123"
ACCESS_POINT_ARN = f"{FILE_SYSTEM_ARN}/access-point/ap-org-a"
BOOTSTRAP_SECRET_ARN = (
    "arn:aws:secretsmanager:eu-west-2:123456789012:secret:"
    "opensre/tenants/org-a/credentials-api-bootstrap"
)
PUBLIC_SECRET_ARN = (
    "arn:aws:secretsmanager:eu-west-2:123456789012:secret:opensre/tenants/org-a/public-api"
)
TASK_ROLE_ARN = "arn:aws:iam::123456789012:role/opensre-org-a-gateway-task"
TASK_DEFINITION_ARN = "arn:aws:ecs:eu-west-2:123456789012:task-definition/opensre-org-a-gateway:1"
SERVICE_ARN = "arn:aws:ecs:eu-west-2:123456789012:service/opensre/org-a"
IMAGE = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/opensre@sha256:" + ("a" * 64)


class MemoryLifecycleRepository:
    """Small fake that also proves no bearer is stored in control-plane records."""

    def __init__(self) -> None:
        self.deployments: dict[str, TenantDeployment] = {}
        self.credentials: dict[str, TenantApiCredential] = {}

    def upsert_tenant_deployment(self, deployment: TenantDeployment) -> TenantDeployment:
        self.deployments[deployment.organization_id] = deployment
        return deployment

    def fetch_tenant_deployment_by_organization_id(
        self,
        organization_id: str,
    ) -> TenantDeployment | None:
        return self.deployments.get(organization_id)

    def list_tenant_deployments(self) -> tuple[TenantDeployment, ...]:
        return tuple(
            self.deployments[organization_id] for organization_id in sorted(self.deployments)
        )

    def upsert_tenant_api_credential(
        self,
        credential: TenantApiCredential,
    ) -> TenantApiCredential:
        self.credentials[credential.key_id] = credential
        return credential

    def fetch_tenant_api_credential_by_key_id(
        self,
        key_id: str,
    ) -> TenantApiCredential | None:
        return self.credentials.get(key_id)

    def disable_active_tenant_api_credentials_for_organization(
        self,
        organization_id: str,
    ) -> int:
        disabled = 0
        for key_id, credential in tuple(self.credentials.items()):
            if credential.organization_id == organization_id and credential.enabled:
                self.credentials[key_id] = replace(
                    credential,
                    enabled=False,
                    updated_at=NOW,
                )
                disabled += 1
        return disabled

    def count_deployments_with_running_desired_state(
        self,
        *,
        exclude_organization_id: str | None = None,
    ) -> int:
        return sum(
            deployment.desired_state is DeploymentDesiredState.RUNNING
            and deployment.organization_id != exclude_organization_id
            for deployment in self.deployments.values()
        )

    @contextmanager
    def with_filesystem_isolation_lock(self) -> Iterator[None]:
        yield


def _config() -> TenantFleetConfig:
    return TenantFleetConfig(
        cluster_arn="arn:aws:ecs:eu-west-2:123456789012:cluster/opensre",
        file_system_id="fs-123",
        file_system_arn=FILE_SYSTEM_ARN,
        gateway_image=IMAGE,
        execution_role_arn="arn:aws:iam::123456789012:role/ecs-execution",
        credentials_api_url="https://credentials.example.test",
        log_group="/opensre/gateways",
        aws_region="eu-west-2",
        subnet_ids=("subnet-a", "subnet-b"),
        security_group_ids=("sg-gateway",),
        mount_security_group_ids=("sg-s3files-mount",),
    )


def _factory_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "DATABASE_URL": "postgresql://neon.example/opensre",
        "AWS_REGION": "eu-west-2",
        "OPENSRE_FARGATE_CLUSTER_ARN": ("arn:aws:ecs:eu-west-2:123456789012:cluster/opensre"),
        "OPENSRE_S3_FILESYSTEM_ID": "fs-123",
        "OPENSRE_S3_FILESYSTEM_ARN": FILE_SYSTEM_ARN,
        "OPENSRE_GATEWAY_IMAGE": IMAGE,
        "OPENSRE_ECS_EXECUTION_ROLE_ARN": ("arn:aws:iam::123456789012:role/ecs-execution"),
        "OPENSRE_CREDENTIALS_API_URL": "https://credentials.example.test",
        "OPENSRE_GATEWAY_LOG_GROUP": "/opensre/gateways",
        "OPENSRE_FARGATE_SUBNET_IDS": "subnet-a,subnet-b",
        "OPENSRE_FARGATE_SECURITY_GROUP_IDS": "sg-gateway",
        "OPENSRE_S3_FILES_MOUNT_SECURITY_GROUP_IDS": "sg-s3files-mount",
        "OPENSRE_MAX_ACTIVE_ORGANIZATIONS": "7",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _dependencies() -> tuple[
    MemoryLifecycleRepository,
    MagicMock,
    MagicMock,
    MagicMock,
    MagicMock,
]:
    repository = MemoryLifecycleRepository()
    s3_files = MagicMock(spec=S3FilesAdapter)
    s3_files.create_tenant_access_point.return_value = S3FilesAccessPoint(
        access_point_id="ap-org-a",
        access_point_arn=ACCESS_POINT_ARN,
    )
    iam = MagicMock(spec=TenantIamAdapter)
    iam.ensure_task_role.return_value = TASK_ROLE_ARN
    secrets = MagicMock(spec=TenantSecretsManagerAdapter)
    secrets.describe_secret_arn.side_effect = lambda secret_id: (
        PUBLIC_SECRET_ARN if "public-api" in secret_id else BOOTSTRAP_SECRET_ARN
    )
    secrets.secret_exists.side_effect = lambda secret_id: "credentials-api-bootstrap" in secret_id
    secrets.create_public_api_credential.return_value = TenantPublicApiCredential(
        key_id="unused-by-lifecycle-test",
        secret_arn=PUBLIC_SECRET_ARN,
        bearer_token="osre_test.initial-secret",
    )
    ecs = MagicMock(spec=TenantEcsAdapter)
    ecs.register_gateway_task_definition.return_value = TASK_DEFINITION_ARN
    ecs.describe_service.return_value = None
    ecs.create_service.return_value = SERVICE_ARN
    return repository, s3_files, iam, secrets, ecs


def _lifecycle(
    repository: MemoryLifecycleRepository,
    s3_files: MagicMock,
    iam: MagicMock,
    secrets: MagicMock,
    ecs: MagicMock,
) -> FargateContainerLifecycle:
    return FargateContainerLifecycle(
        config=_config(),
        repository=repository,
        s3_files=s3_files,
        iam=iam,
        secrets=secrets,
        ecs=ecs,
        clock=lambda: NOW,
    )


def _provisioned() -> tuple[
    FargateContainerLifecycle,
    MemoryLifecycleRepository,
    MagicMock,
    MagicMock,
    MagicMock,
    MagicMock,
]:
    repository, s3_files, iam, secrets, ecs = _dependencies()
    lifecycle = _lifecycle(repository, s3_files, iam, secrets, ecs)
    lifecycle.provision_gateway("org-a", SizeProfile.SMALL)
    return lifecycle, repository, s3_files, iam, secrets, ecs


def test_provision_reconciles_full_boundary_and_returns_bearer_once() -> None:
    repository, s3_files, iam, secrets, ecs = _dependencies()
    lifecycle = _lifecycle(repository, s3_files, iam, secrets, ecs)

    result = lifecycle.provision_gateway("org-a", SizeProfile.SMALL)

    assert result.api_credential == "osre_test.initial-secret"
    assert result.deployment.desired_state is DeploymentDesiredState.RUNNING
    assert result.deployment.actual_state is DeploymentActualState.PROVISIONING
    assert result.deployment.s3_filesystem_arn == FILE_SYSTEM_ARN
    assert result.deployment.s3_access_point_arn == ACCESS_POINT_ARN
    assert result.deployment.task_role_arn == TASK_ROLE_ARN
    assert result.deployment.bootstrap_secret_arn == BOOTSTRAP_SECRET_ARN
    assert result.deployment.task_definition_arn == TASK_DEFINITION_ARN
    assert result.deployment.service_arn == SERVICE_ARN

    s3_files.ensure_mount_targets.assert_called_once_with(
        file_system_id="fs-123",
        subnet_ids=("subnet-a", "subnet-b"),
        security_group_ids=("sg-s3files-mount",),
    )
    s3_files.put_isolation_policy.assert_called_with(
        file_system_id="fs-123",
        file_system_arn=FILE_SYSTEM_ARN,
        tenant_bindings=(TenantMountBinding(TASK_ROLE_ARN, ACCESS_POINT_ARN),),
    )
    access_point_request = s3_files.create_tenant_access_point.call_args.kwargs
    assert access_point_request["organization_id"] == "org-a"
    assert access_point_request["uid"] == 1000
    assert access_point_request["gid"] == 1000
    role_request = iam.ensure_task_role.call_args.kwargs
    assert role_request["access_point_arn"] == ACCESS_POINT_ARN
    assert role_request["bootstrap_secret_arn"] == BOOTSTRAP_SECRET_ARN
    task_spec = ecs.register_gateway_task_definition.call_args.args[0]
    assert task_spec.access_point_arn == ACCESS_POINT_ARN
    assert task_spec.bootstrap_secret_arn == BOOTSTRAP_SECRET_ARN
    assert task_spec.task_role_arn == TASK_ROLE_ARN
    assert task_spec.execution_role_arn != TASK_ROLE_ARN
    assert task_spec.size_profile == "SMALL"
    secrets.enable_secret_access.assert_called_with(
        "opensre/tenants/org-a/credentials-api-bootstrap"
    )

    stored_credential = next(iter(repository.credentials.values()))
    assert stored_credential.secret_arn == PUBLIC_SECRET_ARN
    assert "initial-secret" not in repr(stored_credential)


def test_repeated_provision_reuses_task_service_and_public_secret() -> None:
    lifecycle, _, s3_files, iam, secrets, ecs = _provisioned()
    ecs.task_definition_uses_image.return_value = True
    ecs.describe_service.return_value = FargateServiceState(
        service_arn=SERVICE_ARN,
        task_definition_arn=TASK_DEFINITION_ARN,
        desired_count=1,
        running_count=1,
        pending_count=0,
        status="ACTIVE",
    )
    secrets.secret_exists.side_effect = lambda _secret_id: True
    secrets.describe_secret_arn.side_effect = lambda secret_id: (
        PUBLIC_SECRET_ARN if "public-api" in secret_id else BOOTSTRAP_SECRET_ARN
    )

    result = lifecycle.provision_gateway("org-a", SizeProfile.SMALL)

    assert result.api_credential is None
    assert result.deployment.actual_state is DeploymentActualState.RUNNING
    assert ecs.register_gateway_task_definition.call_count == 1
    assert ecs.create_service.call_count == 1
    ecs.update_service.assert_not_called()
    assert secrets.create_public_api_credential.call_count == 1
    assert s3_files.create_tenant_access_point.call_count == 2
    assert iam.ensure_task_role.call_count == 2
    first_token = s3_files.create_tenant_access_point.call_args_list[0].kwargs["client_token"]
    second_token = s3_files.create_tenant_access_point.call_args_list[1].kwargs["client_token"]
    assert first_token == second_token


def test_active_organization_cap_blocks_new_compute_but_not_reconciliation() -> None:
    repository, s3_files, iam, secrets, ecs = _dependencies()
    config = replace(_config(), max_active_organizations=1)
    lifecycle = FargateContainerLifecycle(
        config=config,
        repository=repository,
        s3_files=s3_files,
        iam=iam,
        secrets=secrets,
        ecs=ecs,
        clock=lambda: NOW,
    )
    lifecycle.provision_gateway("org-a")
    ecs.task_definition_uses_image.return_value = True
    ecs.describe_service.return_value = FargateServiceState(
        service_arn=SERVICE_ARN,
        task_definition_arn=TASK_DEFINITION_ARN,
        desired_count=1,
        running_count=1,
        pending_count=0,
        status="ACTIVE",
    )
    secrets.secret_exists.side_effect = lambda _secret_id: True

    repeated = lifecycle.provision_gateway("org-a")
    with pytest.raises(LifecycleCapacityError):
        lifecycle.provision_gateway("org-b")

    assert repeated.deployment.organization_id == "org-a"
    assert "org-b" not in repository.deployments


def test_active_organization_cap_is_checked_when_restarting_stopped_tenant() -> None:
    _, repository, s3_files, iam, secrets, ecs = _provisioned()
    repository.deployments["org-a"] = replace(
        repository.deployments["org-a"],
        desired_state=DeploymentDesiredState.STOPPED,
        actual_state=DeploymentActualState.STOPPED,
    )
    repository.deployments["org-b"] = replace(
        repository.deployments["org-a"],
        organization_id="org-b",
        desired_state=DeploymentDesiredState.RUNNING,
        actual_state=DeploymentActualState.RUNNING,
    )
    lifecycle = FargateContainerLifecycle(
        config=replace(_config(), max_active_organizations=1),
        repository=repository,
        s3_files=s3_files,
        iam=iam,
        secrets=secrets,
        ecs=ecs,
        clock=lambda: NOW,
    )

    with pytest.raises(LifecycleCapacityError):
        lifecycle.start_gateway("org-a")


def test_size_change_registers_new_revision_and_updates_service() -> None:
    lifecycle, _, _, _, secrets, ecs = _provisioned()
    ecs.register_gateway_task_definition.return_value = TASK_DEFINITION_ARN.replace(":1", ":2")
    ecs.describe_service.return_value = FargateServiceState(
        service_arn=SERVICE_ARN,
        task_definition_arn=TASK_DEFINITION_ARN,
        desired_count=1,
        running_count=1,
        pending_count=0,
        status="ACTIVE",
    )
    secrets.secret_exists.side_effect = lambda _secret_id: True

    result = lifecycle.provision_gateway("org-a", SizeProfile.MEDIUM)

    assert result.deployment.size_profile is SizeProfile.MEDIUM
    assert result.deployment.task_definition_arn.endswith(":2")
    assert ecs.register_gateway_task_definition.call_count == 2
    ecs.update_service.assert_called_once()
    assert ecs.update_service.call_args.args[0].task_definition_arn.endswith(":2")


def test_image_change_registers_new_revision_and_updates_service() -> None:
    lifecycle, _, _, _, secrets, ecs = _provisioned()
    ecs.task_definition_uses_image.return_value = False
    ecs.register_gateway_task_definition.return_value = TASK_DEFINITION_ARN.replace(":1", ":2")
    ecs.describe_service.return_value = FargateServiceState(
        service_arn=SERVICE_ARN,
        task_definition_arn=TASK_DEFINITION_ARN,
        desired_count=1,
        running_count=1,
        pending_count=0,
        status="ACTIVE",
    )
    secrets.secret_exists.side_effect = lambda _secret_id: True

    result = lifecycle.provision_gateway("org-a", SizeProfile.SMALL)

    assert result.deployment.task_definition_arn.endswith(":2")
    assert ecs.register_gateway_task_definition.call_count == 2
    ecs.update_service.assert_called_once()


def test_stop_and_start_are_idempotent_desired_count_changes() -> None:
    lifecycle, _, _, _, _, ecs = _provisioned()
    running = FargateServiceState(
        service_arn=SERVICE_ARN,
        task_definition_arn=TASK_DEFINITION_ARN,
        desired_count=1,
        running_count=1,
        pending_count=0,
        status="ACTIVE",
    )
    stopped = replace(running, desired_count=0, running_count=0)
    ecs.describe_service.side_effect = [running, stopped, stopped]

    stopped_deployment = lifecycle.stop_gateway("org-a")
    already_stopped = lifecycle.stop_gateway("org-a")
    started_deployment = lifecycle.start_gateway("org-a")

    assert stopped_deployment.desired_state is DeploymentDesiredState.STOPPED
    assert already_stopped.actual_state is DeploymentActualState.STOPPED
    assert started_deployment.desired_state is DeploymentDesiredState.RUNNING
    assert ecs.update_service.call_count == 2
    assert ecs.update_service.call_args_list[0].args[0].desired_count == 0
    assert ecs.update_service.call_args_list[1].args[0].desired_count == 1


def test_status_refreshes_observed_ecs_counts_without_exposing_provider_details() -> None:
    lifecycle, _, _, _, _, ecs = _provisioned()
    ecs.describe_service.return_value = FargateServiceState(
        service_arn=SERVICE_ARN,
        task_definition_arn=TASK_DEFINITION_ARN,
        desired_count=1,
        running_count=1,
        pending_count=0,
        status="ACTIVE",
    )

    deployment = lifecycle.get_gateway("org-a")

    assert deployment is not None
    assert deployment.actual_state is DeploymentActualState.RUNNING
    assert deployment.last_error_code is None


def test_delete_removes_compute_disables_credentials_and_preserves_storage() -> None:
    lifecycle, repository, s3_files, iam, secrets, ecs = _provisioned()
    ecs.describe_service.return_value = FargateServiceState(
        service_arn=SERVICE_ARN,
        task_definition_arn=TASK_DEFINITION_ARN,
        desired_count=1,
        running_count=1,
        pending_count=0,
        status="ACTIVE",
    )
    secrets.secret_exists.side_effect = lambda _secret_id: True

    deleted = lifecycle.delete_gateway("org-a")
    repeated = lifecycle.delete_gateway("org-a")

    assert deleted.actual_state is DeploymentActualState.DELETED
    assert deleted.service_arn is None
    assert deleted.task_definition_arn is None
    assert deleted.s3_filesystem_arn == FILE_SYSTEM_ARN
    assert deleted.s3_access_point_arn == ACCESS_POINT_ARN
    assert deleted.task_role_arn == TASK_ROLE_ARN
    assert repeated == deleted
    ecs.delete_service.assert_called_once()
    ecs.deregister_task_definition.assert_called_once_with(TASK_DEFINITION_ARN)
    assert next(iter(repository.credentials.values())).enabled is False
    disabled_tag_calls = [
        call
        for call in secrets.ensure_secret_tags.call_args_list
        if call.args[1]["enabled"] == "false"
    ]
    assert {call.args[0] for call in disabled_tag_calls} == {
        PUBLIC_SECRET_ARN,
        BOOTSTRAP_SECRET_ARN,
    }
    assert {call.args[0] for call in secrets.disable_secret_access.call_args_list} == {
        PUBLIC_SECRET_ARN,
        BOOTSTRAP_SECRET_ARN,
    }
    assert s3_files.method_calls.count(("delete_access_point", (), {})) == 0
    assert iam.method_calls.count(("delete_task_role", (), {})) == 0


def test_rotation_returns_plaintext_once_but_persists_only_metadata() -> None:
    lifecycle, repository, _, _, secrets, _ = _provisioned()
    stored = next(iter(repository.credentials.values()))
    secrets.rotate_public_api_credential.return_value = TenantPublicApiCredential(
        key_id=stored.key_id,
        secret_arn=PUBLIC_SECRET_ARN,
        bearer_token="osre_test.rotated-secret",
    )

    result = lifecycle.rotate_api_credential("org-a")

    assert result.key_id == stored.key_id
    assert result.api_credential == "osre_test.rotated-secret"
    updated = repository.credentials[stored.key_id]
    assert updated.rotated_at == NOW
    assert updated.enabled is True
    assert "rotated-secret" not in repr(updated)


def test_provider_failure_is_recorded_and_externally_generic() -> None:
    repository, s3_files, iam, secrets, ecs = _dependencies()
    s3_files.create_tenant_access_point.side_effect = RuntimeError(
        "provider detail containing a-secret"
    )
    lifecycle = _lifecycle(repository, s3_files, iam, secrets, ecs)

    with pytest.raises(LifecycleOperationError) as raised:
        lifecycle.provision_gateway("org-a", SizeProfile.SMALL)

    assert str(raised.value) == "Gateway lifecycle operation failed"
    assert "a-secret" not in str(raised.value)
    assert repository.deployments["org-a"].last_error_code == "GATEWAY_RECONCILE_FAILED"
    assert repository.deployments["org-a"].actual_state is DeploymentActualState.FAILED


def test_rejects_organization_ids_that_could_escape_resource_names() -> None:
    repository, s3_files, iam, secrets, ecs = _dependencies()
    lifecycle = _lifecycle(repository, s3_files, iam, secrets, ecs)

    with pytest.raises(LifecycleValidationError):
        lifecycle.provision_gateway("../org-a", SizeProfile.SMALL)

    s3_files.create_tenant_access_point.assert_not_called()


def test_zero_argument_factory_uses_real_environment_and_standard_aws_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import platform.deployment_multi_tenant.lambda_control_plane.methods.fargate_container_service as lifecycle_module

    _factory_environment(monkeypatch)
    repository = MemoryLifecycleRepository()
    database_urls: list[str] = []
    initialize_schema_values: list[bool] = []
    clients: list[tuple[str, str]] = []

    def store(
        database_url: str,
        *,
        initialize_schema: bool,
    ) -> MemoryLifecycleRepository:
        database_urls.append(database_url)
        initialize_schema_values.append(initialize_schema)
        return repository

    def client(service: str, region: str) -> object:
        clients.append((service, region))
        return object()

    monkeypatch.setattr(lifecycle_module, "ControlPlaneDbClient", store)
    monkeypatch.setattr(lifecycle_module, "create_boto3_client", client)

    lifecycle = lifecycle_module.create_fargate_container_lifecycle_from_environment()

    assert isinstance(lifecycle, FargateContainerLifecycle)
    assert database_urls == ["postgresql://neon.example/opensre"]
    assert initialize_schema_values == [False]
    assert clients == [
        ("s3files", "eu-west-2"),
        ("iam", "eu-west-2"),
        ("secretsmanager", "eu-west-2"),
        ("ecs", "eu-west-2"),
    ]
    config = TenantFleetConfig.from_environment()
    assert config.subnet_ids == ("subnet-a", "subnet-b")
    assert config.max_active_organizations == 7


def test_factory_environment_has_no_demo_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _factory_environment(monkeypatch)
    monkeypatch.delenv("OPENSRE_GATEWAY_IMAGE")

    with pytest.raises(RuntimeError, match="OPENSRE_GATEWAY_IMAGE"):
        TenantFleetConfig.from_environment()


def test_credentials_api_url_sentinel_normalizes_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fleet stack exports "-" when CredentialsApiUrl is unset."""
    _factory_environment(monkeypatch)
    monkeypatch.setenv("OPENSRE_CREDENTIALS_API_URL", "-")

    config = TenantFleetConfig.from_environment()

    assert config.credentials_api_url == ""


def test_ecs_delete_and_deregister_tolerate_absent_resources() -> None:
    ecs_client = MagicMock()
    ecs_client.describe_services.return_value = {"services": [], "failures": []}
    ecs_client.describe_task_definition.side_effect = ClientError(
        {"Error": {"Code": "ClientException", "Message": "not found"}},
        "DescribeTaskDefinition",
    )
    adapter = TenantEcsAdapter(ecs_client)

    adapter.delete_service(cluster="opensre", service_name="org-a")
    adapter.deregister_task_definition(TASK_DEFINITION_ARN)

    ecs_client.delete_service.assert_not_called()
    ecs_client.deregister_task_definition.assert_not_called()
