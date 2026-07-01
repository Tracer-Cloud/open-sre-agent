"""EC2 deployment stack constants (web + gateway containers on one instance)."""

from __future__ import annotations

from dataclasses import dataclass

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
