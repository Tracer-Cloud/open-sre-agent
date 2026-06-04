from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RemediationActionType(StrEnum):
    kubectl_restart_deployment = "kubectl_restart_deployment"
    kubectl_rollback_deployment = "kubectl_rollback_deployment"
    kubectl_scale_deployment = "kubectl_scale_deployment"
    helm_rollback_release = "helm_rollback_release"
    argocd_sync_application = "argocd_sync_application"
    aws_restart_rds_instance = "aws_restart_rds_instance"
    aws_scale_asg = "aws_scale_asg"
    aws_restart_ecs_service = "aws_restart_ecs_service"
    sql_terminate_connections = "sql_terminate_connections"
    generic_shell = "generic_shell"
    manual_step = "manual_step"


class SafetyLevel(StrEnum):
    safe = "safe"
    elevated = "elevated"
    manual = "manual"


SAFETY_BY_ACTION_TYPE: dict[RemediationActionType, SafetyLevel] = {
    RemediationActionType.kubectl_restart_deployment: SafetyLevel.elevated,
    RemediationActionType.kubectl_rollback_deployment: SafetyLevel.elevated,
    RemediationActionType.kubectl_scale_deployment: SafetyLevel.elevated,
    RemediationActionType.helm_rollback_release: SafetyLevel.elevated,
    RemediationActionType.argocd_sync_application: SafetyLevel.elevated,
    RemediationActionType.aws_restart_rds_instance: SafetyLevel.elevated,
    RemediationActionType.aws_scale_asg: SafetyLevel.elevated,
    RemediationActionType.aws_restart_ecs_service: SafetyLevel.elevated,
    RemediationActionType.sql_terminate_connections: SafetyLevel.elevated,
    RemediationActionType.generic_shell: SafetyLevel.elevated,
    RemediationActionType.manual_step: SafetyLevel.manual,
}


@dataclass
class RemediationAction:
    action_type: RemediationActionType
    description: str
    command: str
    safety_level: SafetyLevel = SafetyLevel.elevated
    target: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    original_step: str = ""


@dataclass
class RemediationResult:
    action: RemediationAction
    success: bool = False
    output: str = ""
    error: str | None = None
