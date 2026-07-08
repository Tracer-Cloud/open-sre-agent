"""Tests for platform.deployment.aws.ec2.launch_instance block device mapping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from platform.deployment.aws.config import (
    EC2_ROOT_DEVICE_NAME,
    EC2_UBUNTU_ROOT_DEVICE_NAME,
    EC2_VOLUME_SIZE_GB,
    EC2_VOLUME_TYPE,
)
from platform.deployment.aws.ec2 import launch_instance


@patch("platform.deployment.aws.ec2.get_boto3_client")
def test_launch_instance_defaults_to_al2023_root_device(mock_get_boto3_client: MagicMock) -> None:
    ec2 = MagicMock()
    ec2.run_instances.return_value = {"Instances": [{"InstanceId": "i-al2023"}]}
    mock_get_boto3_client.return_value = ec2

    launch_instance(
        "ami-al2023",
        "arn:aws:iam::1:instance-profile/p",
        "test-stack",
        region="us-east-1",
    )

    mapping = ec2.run_instances.call_args.kwargs["BlockDeviceMappings"][0]
    assert mapping["DeviceName"] == EC2_ROOT_DEVICE_NAME
    assert mapping["Ebs"] == {
        "VolumeSize": EC2_VOLUME_SIZE_GB,
        "VolumeType": EC2_VOLUME_TYPE,
    }


@patch("platform.deployment.aws.ec2.get_boto3_client")
def test_launch_instance_accepts_ubuntu_root_device(mock_get_boto3_client: MagicMock) -> None:
    ec2 = MagicMock()
    ec2.run_instances.return_value = {"Instances": [{"InstanceId": "i-ubuntu"}]}
    mock_get_boto3_client.return_value = ec2

    launch_instance(
        "ami-ubuntu2204",
        "arn:aws:iam::1:instance-profile/p",
        "test-stack",
        root_device_name=EC2_UBUNTU_ROOT_DEVICE_NAME,
        region="us-east-1",
    )

    mapping = ec2.run_instances.call_args.kwargs["BlockDeviceMappings"][0]
    assert mapping["DeviceName"] == EC2_UBUNTU_ROOT_DEVICE_NAME
