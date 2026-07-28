"""Contract tests for the injectable AWS Fargate provisioning adapters."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters

from platform.deployment_multi_tenant.lambda_control_plane.aws.ecs import (
    FargateServiceSpec,
    GatewayTaskDefinitionSpec,
    TenantEcsAdapter,
    validate_immutable_image,
)
from platform.deployment_multi_tenant.lambda_control_plane.aws.iam import (
    S3_FILES_CLIENT_ACTIONS,
    TenantIamAdapter,
    TenantMountBinding,
    build_file_system_isolation_policy,
    build_tenant_task_policy,
)
from platform.deployment_multi_tenant.lambda_control_plane.aws.s3_files import S3FilesAdapter
from platform.deployment_multi_tenant.lambda_control_plane.aws.secrets_manager import (
    TenantSecretsManagerAdapter,
)
from platform.deployment_multi_tenant.utils.validate_existing_infrastructure import (
    AwsResourceConfigurationError,
    ExistingInfrastructureValidator,
)

FILE_SYSTEM_ARN = "arn:aws:s3files:eu-west-2:123456789012:file-system/fs-123"
ACCESS_POINT_A_ARN = f"{FILE_SYSTEM_ARN}/access-point/ap-a"
ACCESS_POINT_B_ARN = f"{FILE_SYSTEM_ARN}/access-point/ap-b"
BOOTSTRAP_SECRET_ARN = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:opensre/bootstrap-a"
TASK_ROLE_A_ARN = "arn:aws:iam::123456789012:role/opensre-tenant-a"
IMAGE_DIGEST = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/opensre/gateway@sha256:" + ("a" * 64)


def _validate_sdk_request(
    service: str,
    operation: str,
    parameters: dict[str, object],
) -> None:
    """Validate fake-client calls against the installed botocore service model."""
    client = boto3.client(  # type: ignore[call-overload]
        service,
        region_name="eu-west-2",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    input_shape = client.meta.service_model.operation_model(operation).input_shape
    assert input_shape is not None
    validate_parameters(parameters, input_shape)


def _infrastructure_clients() -> tuple[MagicMock, MagicMock, MagicMock]:
    ec2 = MagicMock()
    ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-1", "State": "available"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {
                "SubnetId": "subnet-a",
                "VpcId": "vpc-1",
                "State": "available",
            },
            {
                "SubnetId": "subnet-b",
                "VpcId": "vpc-1",
                "State": "available",
            },
        ]
    }
    ecr = MagicMock()
    ecr.describe_repositories.return_value = {
        "repositories": [
            {
                "repositoryArn": "arn:aws:ecr:eu-west-2:123456789012:repository/opensre",
                "repositoryUri": "123456789012.dkr.ecr.eu-west-2.amazonaws.com/opensre",
            }
        ]
    }
    s3 = MagicMock()
    s3.head_bucket.return_value = {}
    s3.get_bucket_encryption.return_value = {
        "ServerSideEncryptionConfiguration": {
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": "aws:kms",
                        "KMSMasterKeyID": "arn:aws:kms:eu-west-2:123456789012:key/key-id",
                    }
                }
            ]
        }
    }
    s3.get_public_access_block.return_value = {
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        }
    }
    return ec2, ecr, s3


def test_discovers_only_explicit_existing_infrastructure() -> None:
    ec2, ecr, s3 = _infrastructure_clients()

    result = ExistingInfrastructureValidator(ec2, ecr, s3).validate(
        vpc_id="vpc-1",
        subnet_ids=("subnet-a", "subnet-b"),
        ecr_repository_name="opensre",
        s3_bucket_name="opensre-files",
    )

    assert result.vpc_id == "vpc-1"
    assert result.subnet_ids == ("subnet-a", "subnet-b")
    assert result.s3_bucket_arn == "arn:aws:s3:::opensre-files"
    assert result.s3_kms_key_id.endswith("key/key-id")
    ec2.describe_vpcs.assert_called_once_with(VpcIds=["vpc-1"])
    ec2.describe_subnets.assert_called_once_with(SubnetIds=["subnet-a", "subnet-b"])
    ecr.describe_repositories.assert_called_once_with(repositoryNames=["opensre"])


@pytest.mark.parametrize("bad_setting", ["encryption", "public_access", "subnet_vpc"])
def test_rejects_unsafe_existing_infrastructure(bad_setting: str) -> None:
    ec2, ecr, s3 = _infrastructure_clients()
    if bad_setting == "encryption":
        s3.get_bucket_encryption.return_value = {
            "ServerSideEncryptionConfiguration": {
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            }
        }
    elif bad_setting == "public_access":
        s3.get_public_access_block.return_value["PublicAccessBlockConfiguration"][
            "RestrictPublicBuckets"
        ] = False
    else:
        ec2.describe_subnets.return_value["Subnets"][0]["VpcId"] = "vpc-other"

    with pytest.raises(AwsResourceConfigurationError):
        ExistingInfrastructureValidator(ec2, ecr, s3).validate(
            vpc_id="vpc-1",
            subnet_ids=("subnet-a", "subnet-b"),
            ecr_repository_name="opensre",
            s3_bucket_name="opensre-files",
        )


def test_creates_s3_files_resources_with_tenant_root_and_posix_identity() -> None:
    s3files = MagicMock()
    s3files.create_file_system.return_value = {
        "fileSystemId": "fs-123",
        "fileSystemArn": FILE_SYSTEM_ARN,
    }
    s3files.create_mount_target.return_value = {"mountTargetId": "mt-123"}
    s3files.create_access_point.return_value = {
        "accessPointId": "ap-a",
        "accessPointArn": ACCESS_POINT_A_ARN,
    }
    adapter = S3FilesAdapter(s3files)

    filesystem = adapter.create_file_system(
        bucket_arn="arn:aws:s3:::opensre-files",
        prefix="remote-agents/",
        kms_key_id="arn:aws:kms:eu-west-2:123456789012:key/key-id",
        service_role_arn="arn:aws:iam::123456789012:role/opensre-s3-files",
        client_token="opensre-shared-files-v1",
        tags={"organization": "shared", "managed-by": "opensre"},
    )
    mount_target_id = adapter.create_mount_target(
        file_system_id=filesystem.file_system_id,
        subnet_id="subnet-0123456789abcdef0",
        security_group_ids=("sg-0123456789abcdef0",),
    )
    access_point = adapter.create_tenant_access_point(
        file_system_id=filesystem.file_system_id,
        organization_id="org-a",
        uid=10001,
        gid=10001,
        client_token="org-a-v1",
        tags={"organization": "org-a"},
    )

    assert filesystem.file_system_arn == FILE_SYSTEM_ARN
    assert mount_target_id == "mt-123"
    assert access_point.access_point_arn == ACCESS_POINT_A_ARN
    s3files.create_file_system.assert_called_once_with(
        bucket="arn:aws:s3:::opensre-files",
        prefix="remote-agents/",
        clientToken="opensre-shared-files-v1",
        kmsKeyId="arn:aws:kms:eu-west-2:123456789012:key/key-id",
        roleArn="arn:aws:iam::123456789012:role/opensre-s3-files",
        tags=[
            {"key": "managed-by", "value": "opensre"},
            {"key": "organization", "value": "shared"},
        ],
    )
    create_access_point = s3files.create_access_point.call_args.kwargs
    assert create_access_point["rootDirectory"] == {
        "path": "/organizations/org-a",
        "creationPermissions": {
            "ownerUid": 10001,
            "ownerGid": 10001,
            "permissions": "0700",
        },
    }
    assert create_access_point["posixUser"] == {"uid": 10001, "gid": 10001}
    _validate_sdk_request(
        "s3files",
        "CreateFileSystem",
        s3files.create_file_system.call_args.kwargs,
    )
    _validate_sdk_request(
        "s3files",
        "CreateMountTarget",
        s3files.create_mount_target.call_args.kwargs,
    )
    _validate_sdk_request(
        "s3files",
        "CreateAccessPoint",
        s3files.create_access_point.call_args.kwargs,
    )


def test_ensures_only_missing_s3_files_mount_targets() -> None:
    s3files = MagicMock()
    s3files.list_mount_targets.return_value = {
        "mountTargets": [
            {
                "mountTargetId": "mt-a",
                "subnetId": "subnet-a",
                "status": "available",
            }
        ]
    }
    s3files.create_mount_target.return_value = {"mountTargetId": "mt-b"}

    mount_target_ids = S3FilesAdapter(s3files).ensure_mount_targets(
        file_system_id="fs-123",
        subnet_ids=("subnet-a", "subnet-b"),
        security_group_ids=("sg-mount",),
    )

    assert mount_target_ids == ("mt-a", "mt-b")
    s3files.create_mount_target.assert_called_once_with(
        fileSystemId="fs-123",
        subnetId="subnet-b",
        securityGroups=["sg-mount"],
    )


def test_reuses_matching_s3_files_access_point_after_idempotency_conflict() -> None:
    s3files = MagicMock()
    s3files.create_access_point.side_effect = ClientError(
        {"Error": {"Code": "ConflictException", "Message": "already exists"}},
        "CreateAccessPoint",
    )
    s3files.list_access_points.return_value = {
        "accessPoints": [
            {
                "accessPointId": "ap-other",
                "accessPointArn": ACCESS_POINT_B_ARN,
                "status": "available",
                "posixUser": {"uid": 10002, "gid": 10002},
                "rootDirectory": {"path": "/organizations/org-b"},
            },
            {
                "accessPointId": "ap-a",
                "accessPointArn": ACCESS_POINT_A_ARN,
                "status": "available",
                "posixUser": {"uid": 10001, "gid": 10001},
                "rootDirectory": {
                    "path": "/organizations/org-a",
                    "creationPermissions": {
                        "ownerUid": 10001,
                        "ownerGid": 10001,
                        "permissions": "0700",
                    },
                },
            },
        ]
    }

    access_point = S3FilesAdapter(s3files).create_tenant_access_point(
        file_system_id="fs-123",
        organization_id="org-a",
        uid=10001,
        gid=10001,
        client_token="org-a-v1",
        tags={"organization": "org-a"},
    )

    assert access_point.access_point_arn == ACCESS_POINT_A_ARN
    s3files.list_access_points.assert_called_once_with(fileSystemId="fs-123")


def test_identity_and_filesystem_policies_enforce_access_point_isolation() -> None:
    task_policy = build_tenant_task_policy(
        file_system_arn=FILE_SYSTEM_ARN,
        access_point_arn=ACCESS_POINT_A_ARN,
        bootstrap_secret_arn=BOOTSTRAP_SECRET_ARN,
    )
    serialized_task_policy = json.dumps(task_policy)

    assert set(task_policy["Statement"][0]["Action"]) == set(S3_FILES_CLIENT_ACTIONS)
    assert task_policy["Statement"][0]["Condition"] == {
        "ArnEquals": {"s3files:AccessPointArn": ACCESS_POINT_A_ARN}
    }
    assert task_policy["Statement"][1]["Resource"] == BOOTSTRAP_SECRET_ARN
    assert "ClientRootAccess" not in serialized_task_policy
    assert "s3:GetObject" not in serialized_task_policy
    assert ACCESS_POINT_B_ARN not in serialized_task_policy

    filesystem_policy = build_file_system_isolation_policy(
        file_system_arn=FILE_SYSTEM_ARN,
        tenant_bindings=(
            TenantMountBinding(TASK_ROLE_A_ARN, ACCESS_POINT_A_ARN),
            TenantMountBinding(
                "arn:aws:iam::123456789012:role/opensre-tenant-b",
                ACCESS_POINT_B_ARN,
            ),
        ),
    )
    tenant_a_allow = filesystem_policy["Statement"][0]
    assert tenant_a_allow["Principal"] == {"AWS": TASK_ROLE_A_ARN}
    assert tenant_a_allow["Condition"] == {
        "StringEquals": {"s3files:AccessPointArn": ACCESS_POINT_A_ARN}
    }
    tenant_a_deny = filesystem_policy["Statement"][1]
    assert tenant_a_deny["Principal"] == {"AWS": TASK_ROLE_A_ARN}
    assert tenant_a_deny["Action"] == "s3files:Client*"
    assert tenant_a_deny["Condition"] == {
        "StringNotEquals": {"s3files:AccessPointArn": ACCESS_POINT_A_ARN}
    }

    s3files = MagicMock()
    S3FilesAdapter(s3files).put_isolation_policy(
        file_system_id="fs-123",
        file_system_arn=FILE_SYSTEM_ARN,
        tenant_bindings=(TenantMountBinding(TASK_ROLE_A_ARN, ACCESS_POINT_A_ARN),),
    )
    document = json.loads(s3files.put_file_system_policy.call_args.kwargs["policy"])
    assert document["Statement"][0]["Effect"] == "Allow"
    assert document["Statement"][1]["Effect"] == "Deny"


def test_tenant_iam_role_receives_only_mount_write_and_bootstrap_permissions() -> None:
    iam = MagicMock()
    iam.create_role.return_value = {"Role": {"Arn": TASK_ROLE_A_ARN}}

    role_arn = TenantIamAdapter(iam).ensure_task_role(
        role_name="opensre-tenant-a",
        file_system_arn=FILE_SYSTEM_ARN,
        access_point_arn=ACCESS_POINT_A_ARN,
        bootstrap_secret_arn=BOOTSTRAP_SECRET_ARN,
        tags={"organization": "org-a"},
    )

    assert role_arn == TASK_ROLE_A_ARN
    policy = json.loads(iam.put_role_policy.call_args.kwargs["PolicyDocument"])
    actions = {
        action
        for statement in policy["Statement"]
        for action in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    }
    assert actions == {
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "s3files:ClientMount",
        "s3files:ClientWrite",
        "secretsmanager:GetSecretValue",
    }
    assert policy["Statement"][1]["Resource"] == BOOTSTRAP_SECRET_ARN
    _validate_sdk_request("iam", "CreateRole", iam.create_role.call_args.kwargs)
    _validate_sdk_request(
        "iam",
        "PutRolePolicy",
        iam.put_role_policy.call_args.kwargs,
    )


def _task_spec(image: str = IMAGE_DIGEST) -> GatewayTaskDefinitionSpec:
    return GatewayTaskDefinitionSpec(
        family="opensre-org-a",
        organization_id="org-a",
        image=image,
        size_profile="SMALL",
        task_role_arn=TASK_ROLE_A_ARN,
        execution_role_arn="arn:aws:iam::123456789012:role/ecs-execution",
        file_system_arn=FILE_SYSTEM_ARN,
        access_point_arn=ACCESS_POINT_A_ARN,
        bootstrap_secret_arn=BOOTSTRAP_SECRET_ARN,
        credentials_api_url="https://credentials.example.test",
        log_group="/opensre/gateways",
        log_region="eu-west-2",
        tags={"organization": "org-a"},
    )


@pytest.mark.parametrize(
    "image",
    [
        "123456789012.dkr.ecr.eu-west-2.amazonaws.com/opensre/gateway:latest",
        "opensre/gateway@sha256:not-a-digest",
        "opensre/gateway",
    ],
)
def test_rejects_mutable_or_malformed_images(image: str) -> None:
    with pytest.raises(ValueError, match="immutable"):
        validate_immutable_image(image)


def test_registers_secret_safe_s3_files_task_definition() -> None:
    ecs = MagicMock()
    ecs.register_task_definition.return_value = {
        "taskDefinition": {
            "taskDefinitionArn": (
                "arn:aws:ecs:eu-west-2:123456789012:task-definition/opensre-org-a:1"
            )
        }
    }

    arn = TenantEcsAdapter(ecs).register_gateway_task_definition(_task_spec())

    assert arn.endswith("opensre-org-a:1")
    request = ecs.register_task_definition.call_args.kwargs
    assert request["cpu"] == "512"
    assert request["memory"] == "1024"
    assert request["taskRoleArn"] == TASK_ROLE_A_ARN
    assert request["volumes"] == [
        {
            "name": "workspace",
            "s3filesVolumeConfiguration": {
                "fileSystemArn": FILE_SYSTEM_ARN,
                "rootDirectory": "/",
                "transitEncryptionPort": 2049,
                "accessPointArn": ACCESS_POINT_A_ARN,
            },
        }
    ]
    container = request["containerDefinitions"][0]
    environment = {item["name"]: item["value"] for item in container["environment"]}
    assert environment == {
        "AWS_REGION": "eu-west-2",
        "HOME": "/workspace/home",
        "LLM_PROVIDER": "bedrock",
        "MODE": "gateway",
        "OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN": BOOTSTRAP_SECRET_ARN,
        "OPENSRE_CREDENTIALS_API_URL": "https://credentials.example.test",
        "OPENSRE_SIZE_PROFILE": "SMALL",
        "OPENSRE_WORKSPACE": "/workspace/files",
        "ORGANIZATION_ID": "org-a",
    }
    assert "secrets" not in container
    task_json = json.dumps(request)
    assert "osre_" not in task_json
    assert "api_key" not in task_json.lower()
    assert "password" not in task_json.lower()
    _validate_sdk_request("ecs", "RegisterTaskDefinition", request)


def test_task_definition_image_match_checks_gateway_container() -> None:
    image = _task_spec().image
    task_definition_arn = "arn:aws:ecs:eu-west-2:123456789012:task-definition/opensre-org-a:1"
    ecs = MagicMock()
    ecs.describe_task_definition.return_value = {
        "taskDefinition": {
            "containerDefinitions": [
                {"name": "sidecar", "image": "example.invalid/sidecar:latest"},
                {"name": "gateway", "image": image},
            ]
        }
    }
    adapter = TenantEcsAdapter(ecs)

    assert adapter.task_definition_uses_image(task_definition_arn, image)
    assert not adapter.task_definition_uses_image(
        task_definition_arn,
        "123456789012.dkr.ecr.eu-west-2.amazonaws.com/opensre@sha256:" + ("b" * 64),
    )


def test_creates_and_updates_private_fargate_service() -> None:
    ecs = MagicMock()
    ecs.create_service.return_value = {
        "service": {"serviceArn": "arn:aws:ecs:eu-west-2:123456789012:service/opensre/org-a"}
    }
    adapter = TenantEcsAdapter(ecs)
    spec = FargateServiceSpec(
        cluster="opensre",
        service_name="org-a",
        task_definition_arn=("arn:aws:ecs:eu-west-2:123456789012:task-definition/opensre-org-a:1"),
        subnet_ids=("subnet-a", "subnet-b"),
        security_group_ids=("sg-gateway",),
    )

    service_arn = adapter.create_service(spec)
    adapter.update_service(spec)

    assert service_arn.endswith("/opensre/org-a")
    create = ecs.create_service.call_args.kwargs
    assert create["launchType"] == "FARGATE"
    assert create["desiredCount"] == 1
    assert create["networkConfiguration"] == {
        "awsvpcConfiguration": {
            "subnets": ["subnet-a", "subnet-b"],
            "securityGroups": ["sg-gateway"],
            "assignPublicIp": "ENABLED",
        }
    }
    assert ecs.update_service.call_args.kwargs["forceNewDeployment"] is True


def test_secret_adapter_reads_only_metadata_and_generates_public_bearer() -> None:
    secrets_client = MagicMock()
    secrets_client.describe_secret.return_value = {"ARN": BOOTSTRAP_SECRET_ARN}
    secrets_client.create_secret.return_value = {
        "ARN": "arn:aws:secretsmanager:eu-west-2:123456789012:secret:public-org-a"
    }
    adapter = TenantSecretsManagerAdapter(
        secrets_client,
        token_factory=lambda _length: "runtime-random-value",
    )

    bootstrap_arn = adapter.require_bootstrap_secret("bootstrap-org-a")
    credential = adapter.create_public_api_credential(
        secret_name="opensre/public/org-a",
        key_id="key-a",
        tags={"organization": "org-a"},
    )

    assert bootstrap_arn == BOOTSTRAP_SECRET_ARN
    assert credential.bearer_token == "osre_key-a.runtime-random-value"
    secrets_client.describe_secret.assert_called_once_with(SecretId="bootstrap-org-a")
    create = secrets_client.create_secret.call_args.kwargs
    assert create["SecretString"] == credential.bearer_token
    assert create["Tags"] == [{"Key": "organization", "Value": "org-a"}]
    _validate_sdk_request("secretsmanager", "CreateSecret", create)


def test_secret_adapter_disables_reads_with_reversible_resource_policy() -> None:
    secrets_client = MagicMock()
    existing_statement = {
        "Sid": "ExistingAuditPolicy",
        "Effect": "Allow",
        "Principal": {"AWS": "arn:aws:iam::123456789012:role/auditor"},
        "Action": "secretsmanager:DescribeSecret",
        "Resource": "*",
    }
    secrets_client.get_resource_policy.return_value = {
        "ResourcePolicy": json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [existing_statement],
            }
        )
    }
    adapter = TenantSecretsManagerAdapter(secrets_client)

    adapter.disable_secret_access(BOOTSTRAP_SECRET_ARN)

    put_request = secrets_client.put_resource_policy.call_args.kwargs
    policy = json.loads(put_request["ResourcePolicy"])
    assert existing_statement in policy["Statement"]
    disabled = next(
        statement
        for statement in policy["Statement"]
        if statement["Sid"] == "OpenSreTenantCredentialDisabled"
    )
    assert disabled["Effect"] == "Deny"
    assert disabled["Action"] == "secretsmanager:GetSecretValue"
    assert put_request["BlockPublicPolicy"] is True
    _validate_sdk_request("secretsmanager", "PutResourcePolicy", put_request)

    secrets_client.reset_mock()
    secrets_client.get_resource_policy.return_value = {
        "ResourcePolicy": json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [disabled],
            }
        )
    }
    adapter.enable_secret_access(BOOTSTRAP_SECRET_ARN)

    secrets_client.delete_resource_policy.assert_called_once_with(SecretId=BOOTSTRAP_SECRET_ARN)
    secrets_client.put_resource_policy.assert_not_called()
