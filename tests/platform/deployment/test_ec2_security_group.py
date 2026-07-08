"""Tests for stack security group provisioning."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from platform.deployment.aws.config import WEB_API_PORT
from platform.deployment.aws.ec2 import (
    create_stack_security_group,
    delete_stack_security_group,
    get_default_vpc_id,
    stack_security_group_name,
    web_api_ingress_cidr,
)


@patch("platform.deployment.aws.ec2.get_boto3_client")
def test_create_stack_security_group_creates_and_opens_web_api_port(
    mock_get_boto3_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ec2 = MagicMock()
    mock_get_boto3_client.return_value = ec2
    ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-123"}]}
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-new"}
    monkeypatch.delenv("OPENSRE_WEB_API_INGRESS_CIDR", raising=False)

    group_id = create_stack_security_group("opensre-ec2", region="us-east-1")

    assert group_id == "sg-new"
    ec2.create_security_group.assert_called_once()
    ec2.authorize_security_group_ingress.assert_called_once()
    permission = ec2.authorize_security_group_ingress.call_args.kwargs["IpPermissions"][0]
    assert permission["FromPort"] == WEB_API_PORT
    assert permission["ToPort"] == WEB_API_PORT
    assert permission["IpRanges"][0]["CidrIp"] == "0.0.0.0/0"


@patch("platform.deployment.aws.ec2.get_boto3_client")
def test_create_stack_security_group_reuses_existing_group(
    mock_get_boto3_client: MagicMock,
) -> None:
    ec2 = MagicMock()
    mock_get_boto3_client.return_value = ec2
    ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-123"}]}
    ec2.describe_security_groups.side_effect = [
        {"SecurityGroups": [{"GroupId": "sg-existing"}]},
        {
            "SecurityGroups": [
                {
                    "GroupId": "sg-existing",
                    "IpPermissions": [
                        {
                            "IpProtocol": "tcp",
                            "FromPort": WEB_API_PORT,
                            "ToPort": WEB_API_PORT,
                            "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                        }
                    ],
                }
            ]
        },
    ]

    group_id = create_stack_security_group("opensre-ec2", region="us-east-1")

    assert group_id == "sg-existing"
    ec2.create_security_group.assert_not_called()
    ec2.revoke_security_group_ingress.assert_not_called()
    ec2.authorize_security_group_ingress.assert_not_called()


@patch("platform.deployment.aws.ec2.get_boto3_client")
def test_create_stack_security_group_revokes_stale_cidr_on_reuse(
    mock_get_boto3_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ec2 = MagicMock()
    mock_get_boto3_client.return_value = ec2
    ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-123"}]}
    ec2.describe_security_groups.side_effect = [
        {"SecurityGroups": [{"GroupId": "sg-existing"}]},
        {
            "SecurityGroups": [
                {
                    "GroupId": "sg-existing",
                    "IpPermissions": [
                        {
                            "IpProtocol": "tcp",
                            "FromPort": WEB_API_PORT,
                            "ToPort": WEB_API_PORT,
                            "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                        }
                    ],
                }
            ]
        },
    ]
    monkeypatch.setenv("OPENSRE_WEB_API_INGRESS_CIDR", "203.0.113.0/24")

    group_id = create_stack_security_group("opensre-ec2", region="us-east-1")

    assert group_id == "sg-existing"
    ec2.revoke_security_group_ingress.assert_called_once()
    revoked = ec2.revoke_security_group_ingress.call_args.kwargs["IpPermissions"][0]
    assert revoked["IpRanges"] == [{"CidrIp": "0.0.0.0/0"}]
    ec2.authorize_security_group_ingress.assert_called_once()
    authorized = ec2.authorize_security_group_ingress.call_args.kwargs["IpPermissions"][0]
    assert authorized["IpRanges"][0]["CidrIp"] == "203.0.113.0/24"


@patch("platform.deployment.aws.ec2.get_boto3_client")
def test_create_stack_security_group_ignores_duplicate_ingress(
    mock_get_boto3_client: MagicMock,
) -> None:
    ec2 = MagicMock()
    mock_get_boto3_client.return_value = ec2
    ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-123"}]}
    ec2.describe_security_groups.side_effect = [
        {"SecurityGroups": [{"GroupId": "sg-existing"}]},
        {
            "SecurityGroups": [
                {
                    "GroupId": "sg-existing",
                    "IpPermissions": [
                        {
                            "IpProtocol": "tcp",
                            "FromPort": WEB_API_PORT,
                            "ToPort": WEB_API_PORT,
                            "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                        }
                    ],
                }
            ]
        },
    ]

    assert create_stack_security_group("opensre-ec2", region="us-east-1") == "sg-existing"
    ec2.revoke_security_group_ingress.assert_not_called()
    ec2.authorize_security_group_ingress.assert_not_called()


@patch("platform.deployment.aws.ec2.get_boto3_client")
def test_get_default_vpc_id_raises_client_error_when_missing(
    mock_get_boto3_client: MagicMock,
) -> None:
    ec2 = MagicMock()
    mock_get_boto3_client.return_value = ec2
    ec2.describe_vpcs.return_value = {"Vpcs": []}

    with pytest.raises(ClientError) as exc_info:
        get_default_vpc_id(region="us-east-1")

    assert exc_info.value.response["Error"]["Code"] == "DefaultVpcNotFound"


def test_web_api_ingress_cidr_reads_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_WEB_API_INGRESS_CIDR", "203.0.113.0/24")
    assert web_api_ingress_cidr() == "203.0.113.0/24"


def test_stack_security_group_name_is_deterministic() -> None:
    assert stack_security_group_name("opensre-gateway-direct-joe") == (
        "opensre-gateway-direct-joe-sg"
    )


@patch("platform.deployment.aws.ec2.get_boto3_client")
def test_delete_stack_security_group_is_idempotent(mock_get_boto3_client: MagicMock) -> None:
    ec2 = MagicMock()
    mock_get_boto3_client.return_value = ec2
    ec2.delete_security_group.side_effect = ClientError(
        {"Error": {"Code": "InvalidGroup.NotFound", "Message": "missing"}},
        "DeleteSecurityGroup",
    )

    delete_stack_security_group("sg-missing", region="us-east-1")
