from __future__ import annotations

import re
from typing import Any

from app.remediation.models import (
    SAFETY_BY_ACTION_TYPE,
    RemediationAction,
    RemediationActionType,
    SafetyLevel,
)

PatternEntry = tuple[re.Pattern[str], RemediationActionType, str]


_PATTERNS: list[PatternEntry] = [
    # AWS RDS restart — must come before generic restart pattern
    (
        re.compile(
            r"(?:restart|reboot)\s+(?:the\s+)?(?:RDS|rds|database|db)\s+(?:instance\s+)?(?:of\s+)?['\"]?([\w-]+)['\"]?",
            re.IGNORECASE,
        ),
        RemediationActionType.aws_restart_rds_instance,
        "aws rds reboot-db-instance --db-instance-identifier {target}",
    ),
    # AWS ECS restart — must come before generic restart pattern.
    # Captures service name and optional cluster name.
    # Expected forms:
    #   "restart ECS service my-svc"
    #   "restart the ECS service my-svc in cluster my-cluster"
    (
        re.compile(
            r"(?:restart|reboot|update)\s+(?:the\s+)?(?:ECS|ecs)\s+(?:service\s+)?['\"]?([\w.-]+)['\"]?(?:\s+in\s+(?:cluster\s+)?['\"]?([\w.-]+)['\"]?)?",
            re.IGNORECASE,
        ),
        RemediationActionType.aws_restart_ecs_service,
        "aws ecs update-service --cluster {cluster} --service {target} --force-new-deployment",
    ),
    # AWS ASG scale
    (
        re.compile(
            r"(?:scale|increase|decrease)\s+(?:the\s+)?(?:auto[.\s]?scaling\s+group|ASG|asg)\s+(?:of\s+)?['\"]?([\w-]+)['\"]?",
            re.IGNORECASE,
        ),
        RemediationActionType.aws_scale_asg,
        "aws autoscaling update-auto-scaling-group --auto-scaling-group-name {target}",
    ),
    # kubectl rollout restart (supports "deployment/my-app" or "deployment my-app")
    # NOTE: must come BEFORE the undo pattern so "undo" is not captured here.
    (
        re.compile(
            r"(?:kubectl\s+)?rollout\s+restart\s+(?:deployment|deploy)\s*[/\s]+([\w.-]+)",
            re.IGNORECASE,
        ),
        RemediationActionType.kubectl_restart_deployment,
        "kubectl rollout restart deployment/{target}",
    ),
    # kubectl rollout undo (supports "deployment/my-app" or "deployment my-app")
    (
        re.compile(
            r"(?:kubectl\s+)?rollout\s+undo\s+(?:deployment|deploy)\s*[/\s]+([\w.-]+)",
            re.IGNORECASE,
        ),
        RemediationActionType.kubectl_rollback_deployment,
        "kubectl rollout undo deployment/{target}",
    ),
    # Generic restart deployment/pod (for natural language like "Restart the deployment my-app")
    (
        re.compile(
            r"(?:restart|reboot)\s+(?:the\s+)?(?:deployment|pod|service)\s+(?:of\s+)?([\w.-]+)",
            re.IGNORECASE,
        ),
        RemediationActionType.kubectl_restart_deployment,
        "kubectl rollout restart deployment/{target}",
    ),
    # kubectl scale
    (
        re.compile(
            r"(?:kubectl\s+)?scale\s+(?:deployment|deploy)\s*[/\s]+([\w.-]+)\s+--replicas=(\d+)",
            re.IGNORECASE,
        ),
        RemediationActionType.kubectl_scale_deployment,
        "kubectl scale deployment/{target} --replicas={replicas}",
    ),
    # helm rollback
    (
        re.compile(
            r"helm\s+rollback\s+([\w.-]+)",
            re.IGNORECASE,
        ),
        RemediationActionType.helm_rollback_release,
        "helm rollback {target}",
    ),
    # Natural language roll back helm release
    (
        re.compile(
            r"roll\s*(?:back|out)\s+(?:the\s+)?(?:helm\s+)?release\s+([\w.-]+)",
            re.IGNORECASE,
        ),
        RemediationActionType.helm_rollback_release,
        "helm rollback {target}",
    ),
    # argocd app sync
    (
        re.compile(
            r"argocd\s+app\s+sync\s+([\w.-]+)",
            re.IGNORECASE,
        ),
        RemediationActionType.argocd_sync_application,
        "argocd app sync {target}",
    ),
    # SQL terminate connections
    (
        re.compile(
            r"(?:pg_terminate_backend|terminate\s+(?:all\s+)?(?:connections|sessions|backends))",
            re.IGNORECASE,
        ),
        RemediationActionType.sql_terminate_connections,
        "psql -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{database}';\"",
    ),
    # Generic shell — backtick quoted
    (
        re.compile(
            r"run\s+[`'\"](.+?)[`'\"]",
            re.IGNORECASE,
        ),
        RemediationActionType.generic_shell,
        "{command}",
    ),
    # Generic shell — "execute" quoted
    (
        re.compile(
            r"execut(?:e|ing)\s+[`'\"](.+?)[`'\"]",
            re.IGNORECASE,
        ),
        RemediationActionType.generic_shell,
        "{command}",
    ),
]


def classify_remediation_steps(
    steps: list[str],
) -> list[RemediationAction]:
    actions: list[RemediationAction] = []
    for step in steps:
        if not step or not step.strip():
            continue
        action = _classify_single_step(step.strip())
        actions.append(action)
    return actions


def _classify_single_step(step: str) -> RemediationAction:
    for pattern, action_type, command_template in _PATTERNS:
        match = pattern.search(step)
        if not match:
            continue

        params: dict[str, Any] = {}
        target = match.group(1) if match.lastindex and match.lastindex >= 1 else ""

        if action_type is RemediationActionType.kubectl_scale_deployment:
            replicas = match.group(2) if match.lastindex and match.lastindex >= 2 else ""
            params["replicas"] = replicas
            command = command_template.format(target=target, replicas=replicas)
        elif action_type is RemediationActionType.aws_restart_ecs_service:
            cluster = match.group(2) if match.lastindex and match.lastindex >= 2 else ""
            params["cluster"] = cluster
            command = command_template.format(
                target=target,
                cluster=cluster or "default",
            )
        elif action_type is RemediationActionType.generic_shell:
            command = target
            params["command"] = command
        elif action_type is RemediationActionType.sql_terminate_connections:
            command = command_template.format(database=target or "unknown")
        else:
            command = command_template.format(target=target)

        safety_level = SAFETY_BY_ACTION_TYPE.get(action_type, SafetyLevel.elevated)

        return RemediationAction(
            action_type=action_type,
            description=step,
            command=command,
            safety_level=safety_level,
            target=target,
            parameters=params,
            original_step=step,
        )

    return RemediationAction(
        action_type=RemediationActionType.manual_step,
        description=step,
        command="",
        safety_level=SafetyLevel.manual,
        original_step=step,
    )
