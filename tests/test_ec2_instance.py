from __future__ import annotations

from unittest.mock import MagicMock, patch

from platform.deployment import instance as instance_module
from platform.deployment.aws import ec2 as ec2_module


@patch("platform.deployment.aws.ec2.time.sleep", return_value=None)
@patch("platform.deployment.aws.ec2.get_boto3_client")
def test_create_instance_profile_returns_profile_details(
    mock_get_boto3_client: MagicMock,
    _mock_sleep: MagicMock,
) -> None:
    iam = MagicMock()
    iam.create_role.return_value = {"Role": {"Arn": "arn:aws:iam::123:role/test-role"}}
    iam.get_instance_profile.return_value = {
        "InstanceProfile": {"Arn": "arn:aws:iam::123:instance-profile/test-profile"}
    }
    mock_get_boto3_client.return_value = iam

    result = ec2_module.create_instance_profile(
        role_name="test-role",
        profile_name="test-profile",
        stack_name="test-stack",
    )

    assert result["ProfileName"] == "test-profile"
    assert result["ProfileArn"] == "arn:aws:iam::123:instance-profile/test-profile"
    assert result["RoleName"] == "test-role"


def test_generate_user_data_includes_docker_pull() -> None:
    user_data = instance_module.generate_user_data(
        image_uri="123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre:latest",
        log_path="/var/log/opensre-deploy.log",
        web_env_vars={"OPENAI_API_KEY": "sk-123"},
    )

    assert "docker pull" in user_data
    assert "docker run" in user_data
    assert "OPENAI_API_KEY" in user_data
