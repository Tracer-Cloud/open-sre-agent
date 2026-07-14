from __future__ import annotations

from unittest.mock import MagicMock, patch

from platform.deployment.aws import ec2 as ec2_module
from platform.deployment.ecr_deploy import instance as instance_module


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
    _mock_sleep.assert_called_once_with(10)


def test_split_container_env_vars_excludes_messaging_tokens_from_web() -> None:
    web_env, gateway_env = instance_module._split_container_env_vars(
        {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-test",
            "TELEGRAM_BOT_TOKEN": "tg-token",
            "TELEGRAM_ALLOWED_USERS": "123",
            "SLACK_BOT_TOKEN": "xoxb-test",
            "SLACK_APP_TOKEN": "xapp-test",
            "SLACK_ALLOWED_USERS": "U123",
        }
    )

    assert web_env == {"MODE": "web", "LLM_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test"}
    assert gateway_env["MODE"] == "gateway"
    assert gateway_env["TELEGRAM_BOT_TOKEN"] == "tg-token"
    assert gateway_env["SLACK_BOT_TOKEN"] == "xoxb-test"
    assert gateway_env["SLACK_APP_TOKEN"] == "xapp-test"
    assert gateway_env["SLACK_ALLOWED_USERS"] == "U123"


@patch("platform.deployment.ecr_deploy.instance.run_ssm_shell_command")
def test_provision_instance_via_ssm_installs_pulls_and_starts_containers(
    mock_run_ssm: MagicMock,
) -> None:
    mock_run_ssm.return_value = {"status": "Success", "stderr": ""}
    image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre:latest"

    instance_module.provision_instance_via_ssm(
        "i-123",
        image_uri=image_uri,
        container_env_vars={"OPENAI_API_KEY": "sk-with'quote", "TELEGRAM_BOT_TOKEN": "tg-token"},
    )

    assert mock_run_ssm.call_count == 1
    commands = mock_run_ssm.call_args.kwargs["commands"]
    joined = "\n".join(commands)
    assert "dnf install -y docker aws-cli" in joined
    assert "/usr/bin/docker pull" in joined
    assert "base64 -d" in joined
    assert "sk-with'quote" not in joined
    assert "--env-file" in joined
    assert "/usr/bin/docker run" in joined
    assert mock_run_ssm.call_args.kwargs["max_poll_attempts"] == 60
