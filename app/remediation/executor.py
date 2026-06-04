from __future__ import annotations

import logging
import subprocess
from typing import Any

from app.remediation.models import RemediationAction, RemediationActionType, RemediationResult

logger = logging.getLogger(__name__)

_SHELL_TIMEOUT = 60
_MAX_OUTPUT_CHARS = 10_000


def execute_remediation_action(action: RemediationAction) -> RemediationResult:
    if action.action_type is RemediationActionType.manual_step:
        return RemediationResult(
            action=action,
            success=False,
            output="",
            error="Manual step — cannot automate",
        )

    if action.action_type in _AWS_ACTION_TYPES:
        return _execute_aws_action(action)

    if not action.command:
        return RemediationResult(
            action=action,
            success=False,
            output="",
            error="No command to execute",
        )

    return _execute_shell_command(action)


_AWS_ACTION_TYPES = {
    RemediationActionType.aws_restart_rds_instance,
    RemediationActionType.aws_scale_asg,
    RemediationActionType.aws_restart_ecs_service,
}


def _execute_aws_action(action: RemediationAction) -> RemediationResult:
    service_operation_map = {
        RemediationActionType.aws_restart_rds_instance: ("rds", "reboot_db_instance"),
        RemediationActionType.aws_scale_asg: ("autoscaling", "update_auto_scaling_group"),
        RemediationActionType.aws_restart_ecs_service: ("ecs", "update_service"),
    }
    entry = service_operation_map.get(action.action_type)
    if entry is None:
        return RemediationResult(
            action=action,
            success=False,
            output="",
            error=f"No AWS operation mapping for {action.action_type}",
        )

    service_name, operation_name = entry
    aws_params = _build_aws_params(action)

    try:
        import boto3

        client = boto3.client(service_name)  # type: ignore[call-overload]
        method = getattr(client, operation_name)
        result = method(**aws_params)
        return RemediationResult(
            action=action,
            success=True,
            output=str(result),
        )
    except Exception as exc:
        logger.exception("AWS remediation failed: %s", exc)
        return RemediationResult(
            action=action,
            success=False,
            output="",
            error=f"{type(exc).__name__}: {exc}",
        )


def _build_aws_params(action: RemediationAction) -> dict[str, Any]:
    if action.action_type is RemediationActionType.aws_restart_rds_instance:
        return {"DBInstanceIdentifier": action.target}
    if action.action_type is RemediationActionType.aws_scale_asg:
        return {"AutoScalingGroupName": action.target}
    if action.action_type is RemediationActionType.aws_restart_ecs_service:
        return {"service": action.target, "forceNewDeployment": True}
    return {}


def _execute_shell_command(action: RemediationAction) -> RemediationResult:
    try:
        completed = subprocess.run(
            action.command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SHELL_TIMEOUT,
        )
        return RemediationResult(
            action=action,
            success=completed.returncode == 0,
            output=_truncate(completed.stdout),
            error=completed.stderr if completed.returncode != 0 else None,
        )
    except subprocess.TimeoutExpired:
        return RemediationResult(
            action=action,
            success=False,
            output="",
            error=f"Command timed out after {_SHELL_TIMEOUT}s",
        )
    except Exception as exc:
        logger.exception("Shell remediation failed: %s", exc)
        return RemediationResult(
            action=action,
            success=False,
            output="",
            error=f"{type(exc).__name__}: {exc}",
        )


def _truncate(text: str) -> str:
    if len(text) > _MAX_OUTPUT_CHARS:
        return text[:_MAX_OUTPUT_CHARS] + "\n... output truncated ..."
    return text
