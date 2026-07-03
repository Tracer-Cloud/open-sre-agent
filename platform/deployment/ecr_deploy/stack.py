"""EC2 stack configuration and persisted deployment outputs."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.constants import OPENSRE_HOME_DIR

STACK_NAME = "opensre-ec2"
ECR_REPO_NAME = "opensre"
WEB_CONTAINER_NAME = "opensre-web"
GATEWAY_CONTAINER_NAME = "opensre-gateway"
DEPLOY_LOG_PATH = "/var/log/opensre-deploy.log"

_STACK_SUFFIX_ENV = "OPENSRE_STACK_SUFFIX"

_OUTPUTS_DIR = OPENSRE_HOME_DIR / "deployments"
_IMAGE_URI_FILE = _OUTPUTS_DIR / "image-uri.txt"


@dataclass(frozen=True)
class DeployStack:
    """Settings for the unified EC2 deployment."""

    stack_name: str
    ecr_repo_name: str
    web_container_name: str
    gateway_container_name: str
    log_path: str


DEPLOY_STACK = DeployStack(
    stack_name=STACK_NAME,
    ecr_repo_name=ECR_REPO_NAME,
    web_container_name=WEB_CONTAINER_NAME,
    gateway_container_name=GATEWAY_CONTAINER_NAME,
    log_path=DEPLOY_LOG_PATH,
)


def get_stack() -> DeployStack:
    """Return the unified EC2 deployment stack configuration.

    When ``OPENSRE_STACK_SUFFIX`` is set, all resource names are suffixed with
    ``-<value>`` so each developer gets an isolated set of AWS resources (EC2
    instance, IAM role/profile, ECR repo) within a shared account.

    Example — with ``OPENSRE_STACK_SUFFIX=joe``:
        stack_name         → ``opensre-ec2-joe``
        ecr_repo_name      → ``opensre-joe``
        web_container_name → ``opensre-web-joe``
        gateway_container  → ``opensre-gateway-joe``
    """
    suffix = os.getenv(_STACK_SUFFIX_ENV, "").strip()
    if suffix:
        return DeployStack(
            stack_name=f"{STACK_NAME}-{suffix}",
            ecr_repo_name=f"{ECR_REPO_NAME}-{suffix}",
            web_container_name=f"{WEB_CONTAINER_NAME}-{suffix}",
            gateway_container_name=f"{GATEWAY_CONTAINER_NAME}-{suffix}",
            log_path=DEPLOY_LOG_PATH,
        )
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


# ── Image URI state ───────────────────────────────────────────────────────────


def get_image_uri_path(*, path: Path | None = None) -> Path:
    """Return the path where the last-built image URI is persisted."""
    return path if path is not None else _IMAGE_URI_FILE


def save_image_uri(image_uri: str, *, path: Path | None = None) -> Path:
    """Persist the image URI written by ``make build-image``."""
    uri_path = get_image_uri_path(path=path)
    uri_path.parent.mkdir(parents=True, exist_ok=True)
    uri_path.write_text(image_uri.strip() + "\n", encoding="utf-8")
    return uri_path


def load_image_uri(*, path: Path | None = None) -> str:
    """Load the image URI saved by the last ``make build-image`` run.

    Raises:
        FileNotFoundError: When no saved URI exists yet.
    """
    uri_path = get_image_uri_path(path=path)
    if not uri_path.exists():
        raise FileNotFoundError(
            f"No saved image URI found at {uri_path}. "
            "Run `make build-image` first, or set OPENSRE_IMAGE_URI."
        )
    return uri_path.read_text(encoding="utf-8").strip()


def image_uri_exists(*, path: Path | None = None) -> bool:
    """Return True when a saved image URI is available on disk."""
    return get_image_uri_path(path=path).exists()
