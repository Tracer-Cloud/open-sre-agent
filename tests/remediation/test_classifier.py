from __future__ import annotations

from app.remediation.classifier import classify_remediation_steps
from app.remediation.models import RemediationActionType, SafetyLevel


def test_classify_restart_deployment() -> None:
    steps = classify_remediation_steps(["Restart the deployment my-app"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.kubectl_restart_deployment
    assert steps[0].target == "my-app"
    assert "my-app" in steps[0].command


def test_classify_rollout_restart_kubectl() -> None:
    steps = classify_remediation_steps(["kubectl rollout restart deployment/my-service"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.kubectl_restart_deployment
    assert steps[0].target == "my-service"


def test_classify_helm_rollback() -> None:
    steps = classify_remediation_steps(["helm rollback my-release"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.helm_rollback_release
    assert steps[0].target == "my-release"


def test_classify_rollback_release() -> None:
    steps = classify_remediation_steps(["Roll back the helm release my-release"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.helm_rollback_release


def test_classify_argocd_sync() -> None:
    steps = classify_remediation_steps(["argocd app sync my-app"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.argocd_sync_application
    assert steps[0].target == "my-app"


def test_classify_rds_restart() -> None:
    steps = classify_remediation_steps(["Restart the RDS instance my-db-instance"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.aws_restart_rds_instance
    assert steps[0].target == "my-db-instance"


def test_classify_rds_reboot_quoted() -> None:
    steps = classify_remediation_steps(["reboot the database instance 'prod-db-1'"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.aws_restart_rds_instance


def test_classify_ecs_restart() -> None:
    steps = classify_remediation_steps(["Restart the ECS service backend-service"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.aws_restart_ecs_service
    assert steps[0].target == "backend-service"
    assert steps[0].parameters.get("cluster") == ""
    assert "cluster default" in steps[0].command


def test_classify_ecs_restart_with_cluster() -> None:
    steps = classify_remediation_steps(["Restart the ECS service my-svc in cluster prod-cluster"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.aws_restart_ecs_service
    assert steps[0].target == "my-svc"
    assert steps[0].parameters.get("cluster") == "prod-cluster"
    assert "--cluster prod-cluster" in steps[0].command


def test_classify_ecs_restart_update_variant_with_cluster() -> None:
    steps = classify_remediation_steps(["Update the ECS service data-pipeline in cluster staging"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.aws_restart_ecs_service
    assert steps[0].target == "data-pipeline"
    assert steps[0].parameters.get("cluster") == "staging"
    assert "--cluster staging" in steps[0].command


def test_classify_kubectl_scale() -> None:
    steps = classify_remediation_steps(["kubectl scale deployment my-app --replicas=5"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.kubectl_scale_deployment
    assert steps[0].target == "my-app"
    assert "5" in steps[0].command


def test_classify_sql_terminate() -> None:
    steps = classify_remediation_steps(["Run pg_terminate_backend to kill blocking sessions"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.sql_terminate_connections


def test_classify_sql_terminate_with_database() -> None:
    steps = classify_remediation_steps(["Terminate all connections on database prod-db"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.sql_terminate_connections
    assert steps[0].target == "prod-db"
    assert "prod-db" in steps[0].command


def test_classify_sql_terminate_for_database() -> None:
    steps = classify_remediation_steps(["pg_terminate_backend for db my-db"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.sql_terminate_connections
    assert steps[0].target == "my-db"
    assert "my-db" in steps[0].command


def test_classify_sql_terminate_in_database() -> None:
    steps = classify_remediation_steps(["Terminate sessions in database reporting-db"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.sql_terminate_connections
    assert steps[0].target == "reporting-db"


def test_classify_asg_scale_with_capacity() -> None:
    steps = classify_remediation_steps(["Scale ASG my-asg to 5"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.aws_scale_asg
    assert steps[0].target == "my-asg"
    assert steps[0].parameters.get("capacity") == "5"
    assert "--desired-capacity 5" in steps[0].command


def test_classify_asg_scale_long_form_with_capacity() -> None:
    steps = classify_remediation_steps(["Increase the auto scaling group worker-asg to 10"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.aws_scale_asg
    assert steps[0].target == "worker-asg"
    assert steps[0].parameters.get("capacity") == "10"


def test_classify_asg_scale_with_flag_capacity() -> None:
    steps = classify_remediation_steps(["Decrease ASG cache-asg --desired-capacity 3"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.aws_scale_asg
    assert steps[0].target == "cache-asg"
    assert steps[0].parameters.get("capacity") == "3"
    assert "--desired-capacity 3" in steps[0].command


def test_classify_asg_scale_without_capacity_falls_to_manual() -> None:
    steps = classify_remediation_steps(["Scale the ASG my-asg"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.manual_step


def test_classify_generic_shell_safety_is_manual() -> None:
    steps = classify_remediation_steps(["Run `some-command --flag`"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.generic_shell
    assert steps[0].safety_level == SafetyLevel.manual


def test_classify_manual_step() -> None:
    steps = classify_remediation_steps(["Check the application logs for further errors"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.manual_step
    assert steps[0].safety_level == SafetyLevel.manual


def test_classify_generic_shell_backtick() -> None:
    steps = classify_remediation_steps(["Run `kubectl get pods -n default`"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.generic_shell
    assert "kubectl get pods" in steps[0].command


def test_classify_execute_shell() -> None:
    steps = classify_remediation_steps(['Execute "curl -X POST http://health"'])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.generic_shell


def test_classify_multiple_steps() -> None:
    steps = classify_remediation_steps(
        [
            "Restart the deployment my-app",
            "Helm rollback my-release",
            "Check the logs manually",
        ]
    )
    assert len(steps) == 3
    assert steps[0].action_type == RemediationActionType.kubectl_restart_deployment
    assert steps[1].action_type == RemediationActionType.helm_rollback_release
    assert steps[2].action_type == RemediationActionType.manual_step


def test_classify_empty_steps() -> None:
    steps = classify_remediation_steps([])
    assert steps == []


def test_classify_blank_steps() -> None:
    steps = classify_remediation_steps(["", "  ", None])  # type: ignore[list-item]
    assert steps == []


def test_classify_rds_describe_safe() -> None:
    steps = classify_remediation_steps(["Describe the RDS instance my-db"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.aws_describe_rds_instance
    assert steps[0].target == "my-db"
    assert steps[0].safety_level == SafetyLevel.safe


def test_classify_rds_describe_get_safe() -> None:
    steps = classify_remediation_steps(["get the database instance 'prod-db'"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.aws_describe_rds_instance
    assert steps[0].safety_level == SafetyLevel.safe


def test_classify_kubectl_describe_safe() -> None:
    steps = classify_remediation_steps(["describe deployment my-app"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.kubectl_describe_deployment
    assert steps[0].target == "my-app"
    assert steps[0].safety_level == SafetyLevel.safe


def test_classify_describe_deployment_natural_language() -> None:
    steps = classify_remediation_steps(["get details of the deployment payment-service"])
    assert len(steps) == 1
    assert steps[0].action_type == RemediationActionType.kubectl_describe_deployment
    assert steps[0].target == "payment-service"
    assert steps[0].safety_level == SafetyLevel.safe
