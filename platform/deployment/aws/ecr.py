"""ECR repository management and Docker image build/push for OpenSRE deployments."""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from platform.deployment.aws.client import DEFAULT_REGION, get_boto3_client, get_standard_tags
from platform.deployment.aws.config import (
    ECR_DEFAULT_IMAGE_TAG,
    ECR_DOCKER_PLATFORM,
    ECR_IMAGE_TAG_MUTABILITY,
    ECR_SCAN_ON_PUSH,
)


def create_repository(name: str, stack_name: str, region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Create or return an existing ECR repository."""
    ecr_client = get_boto3_client("ecr", region)

    try:
        response = ecr_client.create_repository(
            repositoryName=name,
            imageScanningConfiguration={"scanOnPush": ECR_SCAN_ON_PUSH},
            imageTagMutability=ECR_IMAGE_TAG_MUTABILITY,
            tags=get_standard_tags(stack_name),
        )
        repo = response["repository"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "RepositoryAlreadyExistsException":
            response = ecr_client.describe_repositories(repositoryNames=[name])
            repo = response["repositories"][0]
        else:
            raise

    return {
        "uri": repo["repositoryUri"],
        "arn": repo["repositoryArn"],
        "name": repo["repositoryName"],
    }


def get_login_password(region: str = DEFAULT_REGION) -> str:
    """Get an ECR login password for Docker authentication."""
    ecr_client = get_boto3_client("ecr", region)
    response = ecr_client.get_authorization_token()
    auth_data = response["authorizationData"][0]
    token = base64.b64decode(auth_data["authorizationToken"]).decode()
    return str(token.split(":")[1])


def get_registry_url(region: str = DEFAULT_REGION) -> str:
    """Get the ECR registry URL (without https:// prefix)."""
    ecr_client = get_boto3_client("ecr", region)
    response = ecr_client.get_authorization_token()
    proxy_endpoint = response["authorizationData"][0]["proxyEndpoint"]
    return str(proxy_endpoint).replace("https://", "")


def docker_login(region: str = DEFAULT_REGION) -> None:
    """Authenticate Docker with ECR."""
    password = get_login_password(region)
    registry = get_registry_url(region)
    subprocess.run(
        ["docker", "login", "-u", "AWS", "--password-stdin", registry],
        input=password.encode(),
        check=True,
        capture_output=True,
    )


def build_and_push(
    dockerfile_path: Path,
    repository_uri: str,
    tag: str = ECR_DEFAULT_IMAGE_TAG,
    platform: str = ECR_DOCKER_PLATFORM,
    build_args: dict[str, str] | None = None,
    region: str = DEFAULT_REGION,
    context_dir: Path | None = None,
    extra_tags: list[str] | None = None,
) -> str:
    """Build a Docker image once and push it under one or more tags.

    The image is built a single time and tagged with ``tag`` plus any
    ``extra_tags`` (e.g. a git-sha tag alongside the moving ``latest``).
    Extra tags are pushed first and the primary ``tag`` last, so the moving
    tag only updates once every extra tag is published. Returns the full
    image URI for the primary ``tag``.
    """
    docker_login(region)

    if dockerfile_path.is_file():
        dockerfile = str(dockerfile_path)
        if context_dir is None:
            context_dir = dockerfile_path.parent
    else:
        dockerfile = str(dockerfile_path / "Dockerfile")
        if context_dir is None:
            context_dir = dockerfile_path

    # De-dupe while preserving order (primary tag first) so a sha tag that
    # equals `tag` never double-pushes.
    tags: list[str] = []
    for candidate in [tag, *(extra_tags or [])]:
        if candidate and candidate not in tags:
            tags.append(candidate)
    uris = [f"{repository_uri}:{t}" for t in tags]

    cmd = ["docker", "build", "--platform", platform]
    for uri in uris:
        cmd.extend(["-t", uri])
    cmd.extend(["-f", dockerfile])

    if build_args:
        for key, value in build_args.items():
            cmd.extend(["--build-arg", f"{key}={value}"])

    cmd.append(str(context_dir))

    subprocess.run(cmd, check=True)
    for uri in reversed(uris):
        subprocess.run(["docker", "push", uri], check=True)

    return uris[0]


def get_image_digest(repository_name: str, tag: str, region: str = DEFAULT_REGION) -> str | None:
    """Return the sha256 image digest for ``tag``, or None if the lookup fails."""
    try:
        ecr_client = get_boto3_client("ecr", region)
        details = ecr_client.describe_images(
            repositoryName=repository_name,
            imageIds=[{"imageTag": tag}],
        )["imageDetails"]
        return str(details[0]["imageDigest"]) if details else None
    except Exception:  # noqa: BLE001 — digest is advisory; the push already succeeded
        return None


def delete_repository(name: str, region: str = DEFAULT_REGION) -> None:
    """Delete an ECR repository and all images inside it."""
    ecr_client = get_boto3_client("ecr", region)
    try:
        ecr_client.delete_repository(repositoryName=name, force=True)
    except ClientError as e:
        if e.response["Error"]["Code"] == "RepositoryNotFoundException":
            return
        raise
