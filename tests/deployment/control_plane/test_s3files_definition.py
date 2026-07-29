from __future__ import annotations

import json

import pytest

from platform.deployment_multi_tenant.lambda_control_plane.utils.s3files_definition import (
    validate_task_definition,
    validate_tenant_mount_policy,
)

FILE_SYSTEM_ARN = "arn:aws:s3files:eu-west-1:123456789012:file-system/fs-tenant"
ACCESS_POINT_ARN = f"{FILE_SYSTEM_ARN}/access-point/fsap-tenant-a"
IMAGE = "123456789012.dkr.ecr.eu-west-1.amazonaws.com/opensre@sha256:" + ("a" * 64)


def _task_definition() -> dict[str, object]:
    return {
        "taskRoleArn": "arn:aws:iam::123456789012:role/tenant-a-task",
        "executionRoleArn": "arn:aws:iam::123456789012:role/shared-execution",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "containerDefinitions": [
            {
                "name": "gateway",
                "image": IMAGE,
                "user": "1000:1000",
                "privileged": False,
                "mountPoints": [
                    {
                        "sourceVolume": "tenant-workspace",
                        "containerPath": "/workspace",
                        "readOnly": False,
                    }
                ],
                "environment": [
                    {"name": "HOME", "value": "/workspace/home"},
                    {"name": "OPENSRE_WORKSPACE", "value": "/workspace/files"},
                    {"name": "ORGANIZATION_ID", "value": "tenant-a"},
                    {
                        "name": "OPENSRE_CREDENTIALS_API_URL",
                        "value": "https://credentials.example.invalid",
                    },
                    {
                        "name": "OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN",
                        "value": "arn:aws:secretsmanager:eu-west-1:123456789012:secret:tenant-a",
                    },
                ],
            }
        ],
        "volumes": [
            {
                "name": "tenant-workspace",
                "s3filesVolumeConfiguration": {
                    "fileSystemArn": FILE_SYSTEM_ARN,
                    "rootDirectory": "/",
                    "transitEncryptionPort": 2049,
                    "accessPointArn": ACCESS_POINT_ARN,
                },
            }
        ],
    }


def test_task_definition_uses_access_point_and_no_plaintext_secrets() -> None:
    validate_task_definition(
        _task_definition(),
        file_system_arn=FILE_SYSTEM_ARN,
        access_point_arn=ACCESS_POINT_ARN,
        container_name="gateway",
    )


def test_task_definition_rejects_secret_in_environment() -> None:
    task_definition = _task_definition()
    container = task_definition["containerDefinitions"][0]  # type: ignore[index]
    container["environment"].append({"name": "API_TOKEN", "value": "not-allowed"})  # type: ignore[index]

    with pytest.raises(ValueError, match="secret-like"):
        validate_task_definition(
            task_definition,
            file_system_arn=FILE_SYSTEM_ARN,
            access_point_arn=ACCESS_POINT_ARN,
            container_name="gateway",
        )


def test_task_definition_rejects_filesystem_root_instead_of_access_point() -> None:
    task_definition = _task_definition()
    volume = task_definition["volumes"][0]  # type: ignore[index]
    del volume["s3filesVolumeConfiguration"]["accessPointArn"]  # type: ignore[index]

    with pytest.raises(ValueError, match="access point"):
        validate_task_definition(
            task_definition,
            file_system_arn=FILE_SYSTEM_ARN,
            access_point_arn=ACCESS_POINT_ARN,
            container_name="gateway",
        )


def test_tenant_policy_is_scoped_and_excludes_root_and_direct_s3() -> None:
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3files:ClientMount", "s3files:ClientWrite"],
                "Resource": FILE_SYSTEM_ARN,
                "Condition": {"ArnEquals": {"s3files:AccessPointArn": ACCESS_POINT_ARN}},
            }
        ],
    }

    validate_tenant_mount_policy(policy, access_point_arn=ACCESS_POINT_ARN)
    serialized = json.dumps(policy)
    assert "ClientRootAccess" not in serialized
    assert "PutObject" not in serialized


@pytest.mark.parametrize("action", ["s3files:ClientRootAccess", "s3:PutObject", "s3:GetObject"])
def test_tenant_policy_rejects_root_or_direct_object_access(action: str) -> None:
    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3files:ClientMount", action],
                "Condition": {"StringEquals": {"s3files:AccessPointArn": ACCESS_POINT_ARN}},
            }
        ]
    }

    with pytest.raises(ValueError, match="root or direct"):
        validate_tenant_mount_policy(policy, access_point_arn=ACCESS_POINT_ARN)
