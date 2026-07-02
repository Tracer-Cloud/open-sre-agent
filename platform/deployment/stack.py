"""EC2 stack configuration and persisted deployment outputs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.constants import OPENSRE_HOME_DIR
from platform.deployment.aws.config import DEFAULT_INGRESS_CIDR

STACK_NAME = "opensre-ec2"
ECR_REPO_NAME = "opensre"
WEB_CONTAINER_NAME = "opensre-web"
GATEWAY_CONTAINER_NAME = "opensre-gateway"
DEPLOY_LOG_PATH = "/var/log/opensre-deploy.log"
SECURITY_GROUP_DESCRIPTION = (
    "OpenSRE EC2: inbound HTTP on port 8000 (web); gateway uses outbound-only polling"
)
INGRESS_RULES: list[dict[str, object]] = [
    {
        "port": 8000,
        "cidr": DEFAULT_INGRESS_CIDR,
        "description": "OpenSRE web health API",
    }
]

_OUTPUTS_DIR = OPENSRE_HOME_DIR / "deployments"


@dataclass(frozen=True)
class DeployStack:
    """Settings for the unified EC2 deployment."""

    stack_name: str
    ecr_repo_name: str
    web_container_name: str
    gateway_container_name: str
    log_path: str
    ingress_rules: list[dict[str, object]]
    security_group_description: str


DEPLOY_STACK = DeployStack(
    stack_name=STACK_NAME,
    ecr_repo_name=ECR_REPO_NAME,
    web_container_name=WEB_CONTAINER_NAME,
    gateway_container_name=GATEWAY_CONTAINER_NAME,
    log_path=DEPLOY_LOG_PATH,
    ingress_rules=INGRESS_RULES,
    security_group_description=SECURITY_GROUP_DESCRIPTION,
)


def get_stack() -> DeployStack:
    """Return the unified EC2 deployment stack configuration."""
    return DEPLOY_STACK


def get_outputs_path(*, path: Path | None = None) -> Path:
    """Return the persisted deployment outputs path."""
    if path is not None:
        return path
    stack = get_stack()
    return _OUTPUTS_DIR / f"{stack.stack_name}.json"


def save_outputs(
    outputs: Mapping[str, Any],
    *,
    path: Path | None = None,
) -> Path:
    """Persist deployment outputs to local user state."""
    stack = get_stack()
    payload = dict(outputs)
    payload.setdefault("StackName", stack.stack_name)

    output_path = get_outputs_path(path=path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return output_path


def outputs_exists(*, path: Path | None = None) -> bool:
    """Return True when persisted deployment outputs are on disk."""
    return get_outputs_path(path=path).exists()


def load_outputs(*, path: Path | None = None) -> dict[str, Any]:
    """Load deployment outputs from local user state."""
    stack = get_stack()
    output_path = get_outputs_path(path=path)
    if not output_path.exists():
        raise FileNotFoundError(
            f"No outputs found for stack '{stack.stack_name}'. Deploy the stack first."
        )
    result = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Deployment outputs file is malformed.")
    return result


def delete_outputs(*, path: Path | None = None) -> None:
    """Delete the persisted deployment outputs file."""
    output_path = get_outputs_path(path=path)
    if output_path.exists():
        output_path.unlink()
