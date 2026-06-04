from __future__ import annotations

from unittest.mock import patch

import boto3

from app.remediation.executor import execute_remediation_action
from app.remediation.models import RemediationAction, RemediationActionType, SafetyLevel


def test_execute_aws_ecs_restart_with_cluster() -> None:
    with patch.object(boto3, "client") as mock_boto_client:
        mock_instance = mock_boto_client.return_value
        mock_instance.update_service.return_value = {"service": "my-service"}

        action = RemediationAction(
            action_type=RemediationActionType.aws_restart_ecs_service,
            description="Restart ECS my-service in cluster prod-cluster",
            command="aws ecs update-service --cluster prod-cluster --service my-service --force-new-deployment",
            target="my-service",
            parameters={"cluster": "prod-cluster"},
        )
        result = execute_remediation_action(action)
        assert result.success
        mock_instance.update_service.assert_called_once_with(
            cluster="prod-cluster", service="my-service", forceNewDeployment=True
        )


@patch("app.remediation.executor.subprocess.run")
def test_execute_aws_describe_rds_shell(mock_run) -> None:
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = '{"DBInstances": [{"DBInstanceIdentifier": "my-db"}]}'
    mock_run.return_value.stderr = ""

    action = RemediationAction(
        action_type=RemediationActionType.aws_describe_rds_instance,
        description="Describe RDS instance my-db",
        command="aws rds describe-db-instances --db-instance-identifier my-db",
        target="my-db",
    )
    result = execute_remediation_action(action)
    assert result.success
    assert "DBInstanceIdentifier" in result.output
    mock_run.assert_called_once()


@patch("app.remediation.executor.subprocess.run")
def test_execute_kubectl_describe_deployment_shell(mock_run) -> None:
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "Name: my-app\nReplicas: 3"
    mock_run.return_value.stderr = ""

    action = RemediationAction(
        action_type=RemediationActionType.kubectl_describe_deployment,
        description="Describe deployment my-app",
        command="kubectl describe deployment/my-app",
        target="my-app",
    )
    result = execute_remediation_action(action)
    assert result.success
    assert "Replicas: 3" in result.output
    mock_run.assert_called_once()


def test_execute_manual_step_returns_noop() -> None:
    action = RemediationAction(
        action_type=RemediationActionType.manual_step,
        description="Check logs manually",
        command="",
        safety_level=SafetyLevel.manual,
    )
    result = execute_remediation_action(action)
    assert not result.success
    assert result.error == "Manual step — cannot automate"


def test_execute_no_command_returns_error() -> None:
    action = RemediationAction(
        action_type=RemediationActionType.kubectl_restart_deployment,
        description="Restart deployment",
        command="",
        target="my-app",
    )
    result = execute_remediation_action(action)
    assert not result.success


@patch("app.remediation.executor.subprocess.run")
def test_execute_shell_command_success(mock_run) -> None:
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "deployment rolled out"
    mock_run.return_value.stderr = ""

    action = RemediationAction(
        action_type=RemediationActionType.kubectl_restart_deployment,
        description="Restart deployment my-app",
        command="kubectl rollout restart deployment/my-app",
        target="my-app",
    )
    result = execute_remediation_action(action)
    assert result.success
    assert "deployment rolled out" in result.output


@patch("app.remediation.executor.subprocess.run")
def test_execute_shell_command_failure(mock_run) -> None:
    mock_run.return_value.returncode = 1
    mock_run.return_value.stdout = ""
    mock_run.return_value.stderr = "Error: not found"

    action = RemediationAction(
        action_type=RemediationActionType.kubectl_restart_deployment,
        description="Restart deployment bad-app",
        command="kubectl rollout restart deployment/bad-app",
        target="bad-app",
    )
    result = execute_remediation_action(action)
    assert not result.success
    assert result.error is not None


@patch("app.remediation.executor.subprocess.run")
def test_execute_timeout(mock_run) -> None:
    import subprocess

    mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=60)

    action = RemediationAction(
        action_type=RemediationActionType.kubectl_restart_deployment,
        description="Restart deployment slow-app",
        command="kubectl rollout restart deployment/slow-app",
        target="slow-app",
    )
    result = execute_remediation_action(action)
    assert not result.success
    assert "timed out" in (result.error or "").lower()


@patch("boto3.client")
def test_execute_aws_rds_restart(mock_boto_client) -> None:
    mock_instance = mock_boto_client.return_value
    mock_instance.reboot_db_instance.return_value = {
        "DBInstanceIdentifier": "my-db",
        "Status": "rebooting",
    }

    action = RemediationAction(
        action_type=RemediationActionType.aws_restart_rds_instance,
        description="Restart RDS my-db",
        command="aws rds reboot-db-instance --db-instance-identifier my-db",
        target="my-db",
    )
    result = execute_remediation_action(action)
    assert result.success
    mock_instance.reboot_db_instance.assert_called_once_with(DBInstanceIdentifier="my-db")


@patch("boto3.client")
def test_execute_aws_asg_scale_with_capacity(mock_boto_client) -> None:
    mock_instance = mock_boto_client.return_value
    mock_instance.update_auto_scaling_group.return_value = {
        "AutoScalingGroupName": "my-asg",
    }

    action = RemediationAction(
        action_type=RemediationActionType.aws_scale_asg,
        description="Scale ASG my-asg to 5",
        command="aws autoscaling update-auto-scaling-group --auto-scaling-group-name my-asg --desired-capacity 5",
        target="my-asg",
        parameters={"capacity": "5"},
    )
    result = execute_remediation_action(action)
    assert result.success
    mock_instance.update_auto_scaling_group.assert_called_once_with(
        AutoScalingGroupName="my-asg",
        DesiredCapacity=5,
    )


@patch("boto3.client")
def test_execute_aws_ecs_restart(mock_boto_client) -> None:
    mock_instance = mock_boto_client.return_value
    mock_instance.update_service.return_value = {"service": "my-service"}

    action = RemediationAction(
        action_type=RemediationActionType.aws_restart_ecs_service,
        description="Restart ECS my-service",
        command="aws ecs update-service --service my-service --force-new-deployment",
        target="my-service",
    )
    result = execute_remediation_action(action)
    assert result.success
    mock_instance.update_service.assert_called_once_with(
        cluster="default", service="my-service", forceNewDeployment=True
    )
