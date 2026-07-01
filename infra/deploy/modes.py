"""Deploy mode profiles for EC2 web and gateway runtimes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from infra.aws.config import DEFAULT_INGRESS_CIDR

DeployMode = Literal["web", "gateway"]


@dataclass(frozen=True)
class DeployProfile:
    """Per-mode EC2 deployment settings."""

    mode: DeployMode
    stack_name: str
    ecr_repo_name: str
    container_name: str
    log_path: str
    ingress_rules: list[dict[str, object]]
    security_group_description: str
    require_telegram_token: bool
    use_ssm_health_check: bool


WEB_PROFILE = DeployProfile(
    mode="web",
    stack_name="opensre-ec2-web",
    ecr_repo_name="opensre",
    container_name="opensre-web",
    log_path="/var/log/opensre-deploy.log",
    ingress_rules=[
        {
            "port": 8000,
            "cidr": DEFAULT_INGRESS_CIDR,
            "description": "OpenSRE health API",
        }
    ],
    security_group_description="OpenSRE web EC2: inbound HTTP on port 8000",
    require_telegram_token=False,
    use_ssm_health_check=False,
)

GATEWAY_PROFILE = DeployProfile(
    mode="gateway",
    stack_name="opensre-ec2-gateway",
    ecr_repo_name="opensre-gateway",
    container_name="opensre-gateway",
    log_path="/var/log/gateway-deploy.log",
    ingress_rules=[],
    security_group_description="Gateway EC2: outbound-only (no inbound rules required)",
    require_telegram_token=True,
    use_ssm_health_check=True,
)

_PROFILES: dict[DeployMode, DeployProfile] = {
    "web": WEB_PROFILE,
    "gateway": GATEWAY_PROFILE,
}

GATEWAY_CONTAINER_NAME = GATEWAY_PROFILE.container_name


def resolve_deploy_mode() -> DeployMode:
    """Return deploy mode from OPENSRE_DEPLOY_MODE (default: gateway)."""
    mode = os.getenv("OPENSRE_DEPLOY_MODE", "gateway").strip().lower()
    if mode not in _PROFILES:
        valid = ", ".join(sorted(_PROFILES))
        raise ValueError(f"Invalid OPENSRE_DEPLOY_MODE={mode!r}; expected one of: {valid}")
    return mode  # type: ignore[return-value]


def get_profile(mode: DeployMode | None = None) -> DeployProfile:
    """Return the deployment profile for the given or resolved mode."""
    resolved = mode or resolve_deploy_mode()
    return _PROFILES[resolved]
