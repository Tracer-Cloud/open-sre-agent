"""User-data generation and post-launch health checks for EC2 deployments."""

from __future__ import annotations

import logging
import time

from platform.deployment.aws.client import DEFAULT_REGION
from platform.deployment.aws.config import (
    GATEWAY_HEALTH_MAX_ATTEMPTS,
    GATEWAY_HEALTH_POLL_INTERVAL_SECONDS,
    GATEWAY_LOG_TAIL_LINES,
    GATEWAY_READY_LOG_SENTINEL,
    USER_DATA_ECR_AUTH_MAX_ATTEMPTS,
    USER_DATA_ECR_AUTH_RETRY_SECONDS,
    USER_DATA_IAM_PROPAGATION_SECONDS,
)
from platform.deployment.aws.ssm import run_ssm_shell_command
from platform.deployment.health import poll_deployment_health
from platform.deployment.modes import GATEWAY_CONTAINER_NAME, WEB_CONTAINER_NAME

logger = logging.getLogger(__name__)

__all__ = [
    "GATEWAY_CONTAINER_NAME",
    "WEB_CONTAINER_NAME",
    "generate_user_data",
    "wait_for_deployment_ready",
]


def _format_env_flags(env_vars: dict[str, str]) -> str:
    if not env_vars:
        return ""
    return " " + " ".join(f"-e {k}='{v}'" for k, v in env_vars.items())


def generate_user_data(
    *,
    image_uri: str,
    log_path: str,
    web_env_vars: dict[str, str] | None = None,
    gateway_env_vars: dict[str, str] | None = None,
) -> str:
    """Generate cloud-init user data that starts web and gateway containers."""
    web_flags = f"-e MODE=web -p 8000:8000{_format_env_flags(web_env_vars or {})}"
    gateway_flags = f"-e MODE=gateway{_format_env_flags(gateway_env_vars or {})}"

    ecr_registry = image_uri.split("/")[0]
    ecr_region = DEFAULT_REGION

    return f"""\
#!/bin/bash
exec > {log_path} 2>&1
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

echo "=== Starting web container ==="
docker run -d --name {WEB_CONTAINER_NAME} --restart=unless-stopped {web_flags} {image_uri}

echo "=== Starting gateway container ==="
docker run -d --name {GATEWAY_CONTAINER_NAME} --restart=unless-stopped {gateway_flags} {image_uri}

echo "=== Deployment complete ==="
"""


def wait_for_gateway_process(
    instance_id: str,
    *,
    container_name: str = GATEWAY_CONTAINER_NAME,
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
    instance_id: str,
    public_ip: str,
    region: str = DEFAULT_REGION,
) -> None:
    """Wait until web (HTTP) and gateway (SSM log sentinel) are healthy."""
    print("Waiting for web health endpoint...")
    poll_deployment_health(f"http://{public_ip}:8000")
    print("  - Web: OK")

    print("Waiting for gateway process...")
    wait_for_gateway_process(instance_id, region=region)
    print("  - Gateway: OK")
