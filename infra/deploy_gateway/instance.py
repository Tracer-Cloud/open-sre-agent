"""SSM and user-data helpers for the Telegram Gateway EC2 deployment."""

from __future__ import annotations

import logging
import time

from infra.deploy_gateway.aws_client import DEFAULT_REGION, get_boto3_client

logger = logging.getLogger(__name__)

GATEWAY_CONTAINER_NAME = "opensre-gateway"
GATEWAY_ECR_REPO_NAME = "opensre-gateway"

SSM_REGISTRATION_POLL_INTERVAL = 10
SSM_REGISTRATION_MAX_ATTEMPTS = 30

GATEWAY_POLL_INTERVAL = 15
GATEWAY_MAX_ATTEMPTS = 60

SSM_CMD_POLL_INTERVAL = 5
SSM_CMD_POLL_ATTEMPTS = 24

SSM_MANAGED_POLICY_ARN = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"


def generate_gateway_user_data(image_uri: str, env_vars: dict[str, str] | None = None) -> str:
    """Generate cloud-init user data that pulls the gateway image and starts it."""
    env_flags = ""
    if env_vars:
        env_flags = " ".join(f"-e {k}='{v}'" for k, v in env_vars.items())

    ecr_registry = image_uri.split("/")[0]
    ecr_region = DEFAULT_REGION

    return f"""\
#!/bin/bash
exec > /var/log/gateway-deploy.log 2>&1
set -euo pipefail

echo "=== Installing Docker ==="
dnf install -y docker aws-cli
systemctl enable docker
systemctl start docker

echo "=== Waiting for IAM role to propagate ==="
sleep 15

echo "=== Authenticating with ECR ==="
for i in 1 2 3 4 5; do
  if aws ecr get-login-password --region {ecr_region} | \
     docker login --username AWS --password-stdin {ecr_registry}; then
    break
  fi
  echo "ECR auth attempt $i failed, retrying in 10s..."
  sleep 10
done

echo "=== Pulling gateway image ==="
docker pull {image_uri}

echo "=== Starting gateway container ==="
docker run -d --name {GATEWAY_CONTAINER_NAME} --restart=unless-stopped {env_flags} {image_uri}

echo "=== Gateway deployment complete ==="
"""


def wait_for_ssm_registration(
    instance_id: str,
    region: str = DEFAULT_REGION,
    poll_interval: int = SSM_REGISTRATION_POLL_INTERVAL,
    max_attempts: int = SSM_REGISTRATION_MAX_ATTEMPTS,
) -> bool:
    """Wait until the SSM agent on the instance registers and becomes online."""
    ssm = get_boto3_client("ssm", region)

    for attempt in range(max_attempts):
        try:
            resp = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
            instances = resp.get("InstanceInformationList", [])
            if instances and instances[0].get("PingStatus") == "Online":
                logger.info("SSM agent online for %s after %d attempts", instance_id, attempt + 1)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("SSM describe attempt %d: %s", attempt + 1, exc)

        if attempt < max_attempts - 1:
            time.sleep(poll_interval)

    raise TimeoutError(
        f"SSM agent on {instance_id} did not come online after {max_attempts * poll_interval}s"
    )


def run_ssm_shell_command(
    instance_id: str,
    commands: list[str],
    region: str = DEFAULT_REGION,
    poll_interval: int = SSM_CMD_POLL_INTERVAL,
    max_poll_attempts: int = SSM_CMD_POLL_ATTEMPTS,
) -> dict[str, str]:
    """Execute a shell command on an EC2 instance via SSM Run Command."""
    ssm = get_boto3_client("ssm", region)

    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands},
    )
    command_id = resp["Command"]["CommandId"]
    logger.debug("SSM command %s sent to %s", command_id, instance_id)

    for attempt in range(max_poll_attempts):
        time.sleep(poll_interval)
        try:
            inv = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )
        except ssm.exceptions.InvocationDoesNotExist:
            logger.debug("SSM invocation %s not yet available, retrying...", command_id)
            continue

        status = inv["Status"]
        if status in ("Success", "Failed", "Cancelled", "TimedOut", "Undeliverable"):
            result = {
                "status": status,
                "stdout": inv.get("StandardOutputContent", ""),
                "stderr": inv.get("StandardErrorContent", ""),
            }
            logger.debug(
                "SSM command %s finished: status=%s stdout=%r",
                command_id,
                status,
                result["stdout"][:200],
            )
            return result

        logger.debug(
            "SSM command %s status=%s attempt=%d/%d",
            command_id,
            status,
            attempt + 1,
            max_poll_attempts,
        )

    raise TimeoutError(
        f"SSM command {command_id} on {instance_id} did not complete "
        f"within {max_poll_attempts * poll_interval}s"
    )


def wait_for_gateway_process(
    instance_id: str,
    region: str = DEFAULT_REGION,
    poll_interval: int = GATEWAY_POLL_INTERVAL,
    max_attempts: int = GATEWAY_MAX_ATTEMPTS,
) -> bool:
    """Wait until the gateway container is running and has logged 'polling started'."""
    for attempt in range(max_attempts):
        try:
            result = run_ssm_shell_command(
                instance_id=instance_id,
                commands=[
                    f"docker ps --filter name={GATEWAY_CONTAINER_NAME} --filter status=running -q",
                    f"docker logs --tail 200 {GATEWAY_CONTAINER_NAME} 2>&1 || true",
                ],
                region=region,
            )

            stdout = result["stdout"]
            container_running = bool(stdout.strip().split("\n")[0].strip())
            logs_contain_sentinel = "polling started" in stdout

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
