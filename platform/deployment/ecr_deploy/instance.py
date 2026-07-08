"""SSM provisioning, health polling, and post-launch readiness checks."""

from __future__ import annotations

import base64
import logging
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from platform.deployment.aws.client import DEFAULT_REGION
from platform.deployment.aws.config import (
    DOCKER_BIN,
    GATEWAY_HEALTH_MAX_ATTEMPTS,
    GATEWAY_HEALTH_POLL_INTERVAL_SECONDS,
    GATEWAY_LOG_TAIL_LINES,
    GATEWAY_READY_LOG_SENTINEL,
    PROVISION_ECR_AUTH_MAX_ATTEMPTS,
    PROVISION_ECR_AUTH_RETRY_SECONDS,
    SSM_PROVISION_CMD_POLL_ATTEMPTS,
    SSM_PROVISION_CMD_POLL_INTERVAL_SECONDS,
)
from platform.deployment.aws.ssm import run_ssm_shell_command
from platform.deployment.ecr_deploy.stack import GATEWAY_CONTAINER_NAME, WEB_CONTAINER_NAME

logger = logging.getLogger(__name__)

_ENV_DIR = "/etc/opensre"
_GATEWAY_ONLY_ENV_KEYS = frozenset({"TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS"})

__all__ = [
    "GATEWAY_CONTAINER_NAME",
    "HealthPollStatus",
    "WEB_CONTAINER_NAME",
    "poll_deployment_health",
    "provision_instance_via_ssm",
    "wait_for_deployment_ready",
    "wait_for_web_process",
]


@dataclass(frozen=True)
class HealthPollStatus:
    """Result for a successful health poll."""

    url: str
    attempts: int
    status_code: int
    elapsed_seconds: float


def _build_health_urls(base_url: str) -> tuple[str, ...]:
    """Return health URL candidates for a deployment base URL."""
    stripped = base_url.strip().rstrip("/")
    if stripped.endswith("/health") or stripped.endswith("/ok"):
        return (stripped,)
    return (f"{stripped}/health", f"{stripped}/ok")


def poll_deployment_health(
    base_url: str,
    *,
    interval_seconds: float = 5.0,
    max_attempts: int = 60,
    request_timeout_seconds: float = 5.0,
    http_get: Callable[..., object] = requests.get,
    sleep: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.monotonic,
) -> HealthPollStatus:
    """Poll deployment health with ``/health`` then ``/ok`` fallback.

    Raises:
        TimeoutError: When no candidate endpoint returns HTTP 200 in time.
    """
    urls = _build_health_urls(base_url)
    started = time_fn()
    last_status: int | None = None
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        for url in urls:
            try:
                response = http_get(url, timeout=request_timeout_seconds)
                status_code = int(getattr(response, "status_code", 0))
                if status_code == 200:
                    return HealthPollStatus(
                        url=url,
                        attempts=attempt,
                        status_code=status_code,
                        elapsed_seconds=time_fn() - started,
                    )
                last_status = status_code
            except requests.exceptions.RequestException as exc:
                last_error = str(exc)

        if attempt < max_attempts:
            sleep(max(interval_seconds, 0.0))

    detail = (
        f"last status={last_status}"
        if last_status is not None
        else f"last error={last_error or 'none'}"
    )
    elapsed = time_fn() - started
    raise TimeoutError(
        f"Deployment health check timed out after {elapsed:.1f}s "
        f"({max_attempts} attempts, candidates={list(urls)}, {detail})"
    )


def _require_ssm_success(result: dict[str, str], *, instance_id: str, action: str) -> None:
    status = str(result.get("status", ""))
    if status == "Success":
        return
    stderr = str(result.get("stderr", "")).strip()
    raise RuntimeError(
        f"Failed to {action} on {instance_id}: status={status}, stderr={stderr or 'none'}"
    )


def _env_file_content(env_vars: dict[str, str]) -> str:
    """Return Docker ``--env-file`` content for the given variables."""
    lines: list[str] = []
    for key in sorted(env_vars):
        value = env_vars[key]
        if "\n" in value or "\r" in value:
            raise ValueError(f"Environment variable {key} must not contain newlines")
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def _write_env_file_commands(path: str, content: str) -> list[str]:
    """Return shell commands that write ``content`` to ``path`` via base64."""
    encoded = base64.b64encode(content.encode()).decode("ascii")
    quoted_path = shlex.quote(path)
    return [
        f"echo {shlex.quote(encoded)} | base64 -d > {quoted_path}",
        f"chmod 600 {quoted_path}",
    ]


def _split_container_env_vars(env_vars: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Return web and gateway env files from one collected deploy-env mapping."""
    web_env = {
        "MODE": "web",
        **{key: value for key, value in env_vars.items() if key not in _GATEWAY_ONLY_ENV_KEYS},
    }
    gateway_env = {"MODE": "gateway", **env_vars}
    return web_env, gateway_env


def provision_instance_via_ssm(
    instance_id: str,
    *,
    image_uri: str,
    container_env_vars: dict[str, str] | None = None,
    region: str = DEFAULT_REGION,
) -> None:
    """Install Docker, pull the image, and start web + gateway containers via SSM."""
    ecr_registry = image_uri.split("/")[0]
    ecr_region = DEFAULT_REGION
    docker = shlex.quote(DOCKER_BIN)
    quoted_image = shlex.quote(image_uri)
    quoted_registry = shlex.quote(ecr_registry)
    web_env, gateway_env = _split_container_env_vars(container_env_vars or {})
    web_env_path = f"{_ENV_DIR}/web.env"
    gateway_env_path = f"{_ENV_DIR}/gateway.env"

    commands = [
        "set -euo pipefail",
        "dnf install -y docker aws-cli",
        "systemctl enable docker",
        "systemctl start docker",
        (
            f"for i in $(seq 1 {PROVISION_ECR_AUTH_MAX_ATTEMPTS}); do "
            f"if aws ecr get-login-password --region {ecr_region} | "
            f"{docker} login --username AWS --password-stdin {quoted_registry}; then "
            "break; "
            "fi; "
            f'echo "ECR auth attempt $i failed, retrying in '
            f'{PROVISION_ECR_AUTH_RETRY_SECONDS}s..."; '
            f"sleep {PROVISION_ECR_AUTH_RETRY_SECONDS}; "
            "done"
        ),
        f"{docker} pull {quoted_image}",
        f"mkdir -p {shlex.quote(_ENV_DIR)}",
        *_write_env_file_commands(web_env_path, _env_file_content(web_env)),
        *_write_env_file_commands(gateway_env_path, _env_file_content(gateway_env)),
        (
            f"{docker} run -d --name {WEB_CONTAINER_NAME} --restart=unless-stopped "
            f"-p 8000:8000 --env-file {shlex.quote(web_env_path)} {quoted_image}"
        ),
        (
            f"{docker} run -d --name {GATEWAY_CONTAINER_NAME} --restart=unless-stopped "
            f"--env-file {shlex.quote(gateway_env_path)} {quoted_image}"
        ),
    ]

    result = run_ssm_shell_command(
        instance_id=instance_id,
        commands=commands,
        region=region,
        poll_interval=SSM_PROVISION_CMD_POLL_INTERVAL_SECONDS,
        max_poll_attempts=SSM_PROVISION_CMD_POLL_ATTEMPTS,
    )
    _require_ssm_success(result, instance_id=instance_id, action="provision instance")


def wait_for_gateway_process(
    instance_id: str,
    *,
    container_name: str = GATEWAY_CONTAINER_NAME,
    region: str = DEFAULT_REGION,
    poll_interval: int = GATEWAY_HEALTH_POLL_INTERVAL_SECONDS,
    max_attempts: int = GATEWAY_HEALTH_MAX_ATTEMPTS,
) -> bool:
    """Wait until the gateway container is running and has logged the ready sentinel."""
    docker = shlex.quote(DOCKER_BIN)
    for attempt in range(max_attempts):
        try:
            result = run_ssm_shell_command(
                instance_id=instance_id,
                commands=[
                    f"{docker} ps --filter name={container_name} --filter status=running -q",
                    f"{docker} logs --tail {GATEWAY_LOG_TAIL_LINES} {container_name} 2>&1 || true",
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


def wait_for_web_process(
    instance_id: str,
    *,
    container_name: str = WEB_CONTAINER_NAME,
    region: str = DEFAULT_REGION,
    poll_interval: int = GATEWAY_HEALTH_POLL_INTERVAL_SECONDS,
    max_attempts: int = GATEWAY_HEALTH_MAX_ATTEMPTS,
) -> bool:
    """Wait until the web container is running and responding on localhost:8000 via SSM.

    Uses SSM Run Command to curl the health endpoint inside the instance, so no
    inbound security group rule is required.
    """
    docker = shlex.quote(DOCKER_BIN)
    for attempt in range(max_attempts):
        try:
            result = run_ssm_shell_command(
                instance_id=instance_id,
                commands=[
                    f"{docker} ps --filter name={container_name} --filter status=running -q",
                    (
                        "curl -sf http://localhost:8000/health 2>/dev/null "
                        "|| curl -sf http://localhost:8000/ok 2>/dev/null "
                        "|| true"
                    ),
                ],
                region=region,
            )
            stdout = result["stdout"]
            lines = [line.strip() for line in stdout.strip().splitlines() if line.strip()]
            container_running = bool(lines[0]) if lines else False
            health_ok = bool(lines[1]) if len(lines) > 1 else False

            if container_running and health_ok:
                logger.info(
                    "Web process ready on %s after %d attempts",
                    instance_id,
                    attempt + 1,
                )
                return True

            logger.debug(
                "Web not ready yet (attempt %d/%d): running=%s health=%s",
                attempt + 1,
                max_attempts,
                container_running,
                health_ok,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("SSM web check attempt %d failed: %s", attempt + 1, exc)

        if attempt < max_attempts - 1:
            time.sleep(poll_interval)

    raise TimeoutError(
        f"Web container on {instance_id} did not become ready after {max_attempts * poll_interval}s"
    )


def wait_for_deployment_ready(
    *,
    instance_id: str,
    region: str = DEFAULT_REGION,
) -> None:
    """Wait until web (SSM localhost curl) and gateway (SSM log sentinel) are healthy."""
    print("Waiting for web process...")
    wait_for_web_process(instance_id, region=region)
    print("  - Web: OK")

    print("Waiting for gateway process...")
    wait_for_gateway_process(instance_id, region=region)
    print("  - Gateway: OK")
