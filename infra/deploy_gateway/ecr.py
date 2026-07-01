"""ECR repository and image build/push for the gateway deployment."""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from infra.deploy_gateway.aws_client import (
    DEFAULT_REGION,
    get_boto3_client,
    get_standard_tags,
)


def create_repository(name: str, stack_name: str, region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Create or return an existing ECR repository."""
    ecr_client = get_boto3_client("ecr", region)

    try:
        response = ecr_client.create_repository(
            repositoryName=name,
            imageScanningConfiguration={"scanOnPush": True},
            imageTagMutability="MUTABLE",
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
    """Get ECR login password for docker."""
    ecr_client = get_boto3_client("ecr", region)
    response = ecr_client.get_authorization_token()
    auth_data = response["authorizationData"][0]
    token = base64.b64decode(auth_data["authorizationToken"]).decode()
    return str(token.split(":")[1])


def get_registry_url(region: str = DEFAULT_REGION) -> str:
    """Get ECR registry URL without https:// prefix."""
    ecr_client = get_boto3_client("ecr", region)
    response = ecr_client.get_authorization_token()
    proxy_endpoint = response["authorizationData"][0]["proxyEndpoint"]
    return str(proxy_endpoint).replace("https://", "")


def docker_login(region: str = DEFAULT_REGION) -> None:
    """Perform docker login to ECR."""
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
    tag: str = "latest",
    platform: str = "linux/amd64",
    build_args: dict[str, str] | None = None,
    region: str = DEFAULT_REGION,
    context_dir: Path | None = None,
) -> str:
    """Build and push a Docker image to ECR."""
    docker_login(region)

    if dockerfile_path.is_file():
        dockerfile = str(dockerfile_path)
        if context_dir is None:
            context_dir = dockerfile_path.parent
    else:
        dockerfile = str(dockerfile_path / "Dockerfile")
        if context_dir is None:
            context_dir = dockerfile_path

    full_uri = f"{repository_uri}:{tag}"

    cmd = [
        "docker",
        "build",
        "--platform",
        platform,
        "-t",
        full_uri,
        "-f",
        dockerfile,
    ]

    if build_args:
        for key, value in build_args.items():
            cmd.extend(["--build-arg", f"{key}={value}"])

    cmd.append(str(context_dir))

    subprocess.run(cmd, check=True, capture_output=True)
    subprocess.run(["docker", "push", full_uri], check=True, capture_output=True)

    return full_uri
