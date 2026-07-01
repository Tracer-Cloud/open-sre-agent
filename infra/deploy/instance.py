"""User-data generation and post-launch health checks for EC2 deployments."""

from __future__ import annotations

import logging
import time

from infra.aws.client import DEFAULT_REGION
from infra.aws.config import (
    GATEWAY_HEALTH_MAX_ATTEMPTS,
    GATEWAY_HEALTH_POLL_INTERVAL_SECONDS,
    GATEWAY_LOG_TAIL_LINES,
    GATEWAY_READY_LOG_SENTINEL,
    USER_DATA_ECR_AUTH_MAX_ATTEMPTS,
    USER_DATA_ECR_AUTH_RETRY_SECONDS,
    USER_DATA_IAM_PROPAGATION_SECONDS,
)
from infra.aws.ssm import run_ssm_shell_command
from infra.deploy.health import poll_deployment_health
from infra.deploy.modes import GATEWAY_CONTAINER_NAME, DeployProfile

logger = logging.getLogger(__name__)

__all__ = ["GATEWAY_CONTAINER_NAME", "generate_user_data", "wait_for_deployment_ready"]


def generate_user_data(
    *,
    profile: DeployProfile,
    image_uri: str,
    env_vars: dict[str, str] | None = None,
) -> str:
    """Generate cloud-init user data that pulls the image and starts the container."""
    env_flags = f"-e MODE={profile.mode}"
    if profile.mode == "web":
        env_flags += " -p 8000:8000"
    if env_vars:
        env_flags += " " + " ".join(f"-e {k}='{v}'" for k, v in env_vars.items())

    ecr_registry = image_uri.split("/")[0]
    ecr_region = DEFAULT_REGION

    return f"""\
#!/bin/bash
exec > {profile.log_path} 2>&1
set -euo pipefail

echo "=== Installing Docker ==="
dnf install -y docker aws-cli
systemctl enable docker
systemctl start docker

echo "=== Waiting for IAM role to propagate ==="
sleep {USER_DATA_IAM_PROPAGATION_SECONDS}

echo "=== Authenticating with ECR ==="
for i in $(seq 1 {USER_DATA_ECR_AUTH_MAX_ATTEMPTS}); do
  if aws ecr get-login-password --region {ecr_region} | \
     docker login --username AWS --password-stdin {ecr_registry}; then
    break
  fi
  echo "ECR auth attempt $i failed, retrying in {USER_DATA_ECR_AUTH_RETRY_SECONDS}s..."
  sleep {USER_DATA_ECR_AUTH_RETRY_SECONDS}
done

echo "=== Pulling image ==="
docker pull {image_uri}

echo "=== Starting container ({profile.mode} mode) ==="
docker run -d --name {profile.container_name} --restart=unless-stopped {env_flags} {image_uri}

echo "=== Deployment complete ==="
"""


def wait_for_gateway_process(
    instance_id: str,
    *,
    container_name: str,
    region: str = DEFAULT_REGION,
    poll_interval: int = GATEWAY_HEALTH_POLL_INTERVAL_SECONDS,
    max_attempts: int = GATEWAY_HEALTH_MAX_ATTEMPTS,
) -> bool:
    """Wait until the gateway container is running and has logged the ready sentinel."""
    for attempt in range(max_attempts):
        try:
            result = run_ssm_shell_command(
                instance_id=instance_id,
                commands=[
                    f"docker ps --filter name={container_name} --filter status=running -q",
                    f"docker logs --tail {GATEWAY_LOG_TAIL_LINES} {container_name} 2>&1 || true",
                ],
                region=region,
            )

            stdout = result["stdout"]
            container_running = bool(stdout.strip().split("\n")[0].strip())
            logs_contain_sentinel = GATEWAY_READY_LOG_SENTINEL in stdout

            if container_running and logs_contain_sentinel:
                logger.info(
                    "Gateway process ready on %s after %d attempts",
                    instance_id,
                    attempt + 1,
                )
                return True

            logger.debug(
                "Gateway not ready yet (attempt %d/%d): running=%s sentinel=%s",
                attempt + 1,
                max_attempts,
                container_running,
                logs_contain_sentinel,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("SSM gateway check attempt %d failed: %s", attempt + 1, exc)

        if attempt < max_attempts - 1:
            time.sleep(poll_interval)

    raise TimeoutError(
        f"Gateway container on {instance_id} did not become ready "
        f"after {max_attempts * poll_interval}s"
    )


def wait_for_deployment_ready(
    *,
    profile: DeployProfile,
    instance_id: str,
    public_ip: str,
    region: str = DEFAULT_REGION,
) -> None:
    """Wait until the deployed container is healthy for the selected mode."""
    if profile.use_ssm_health_check:
        wait_for_gateway_process(
            instance_id,
            container_name=profile.container_name,
            region=region,
        )
        return

    poll_deployment_health(f"http://{public_ip}:8000")
