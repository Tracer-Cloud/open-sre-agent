#!/usr/bin/env python3
"""Deploy and destroy OpenSRE on EC2 (web + gateway containers on one instance)."""

from __future__ import annotations

import argparse
import os
import time

from botocore.exceptions import ClientError

from config.constants.paths import REPO_ROOT
from platform.deployment.aws import ecr
from platform.deployment.aws.client import DEFAULT_REGION
from platform.deployment.aws.config import (
    ECR_DEFAULT_IMAGE_TAG,
    ECR_DOCKER_PLATFORM,
    INSTANCE_TYPE,
    SSM_MANAGED_POLICY_ARN,
)
from platform.deployment.aws.ec2 import (
    create_instance_profile,
    create_stack_security_group,
    delete_instance_profile,
    delete_stack_security_group,
    find_stack_instance_ids,
    get_latest_al2023_ami,
    launch_instance,
    terminate_instance,
    wait_for_running,
)
from platform.deployment.aws.ssm import wait_for_ssm_registration
from platform.deployment.ecr_deploy.instance import (
    provision_instance_via_ssm,
    wait_for_deployment_ready,
)
from platform.deployment.ecr_deploy.prep import run_lifecycle_main, validate_deploy_env
from platform.deployment.ecr_deploy.stack import (
    delete_outputs,
    get_stack,
    image_uri_exists,
    load_image_uri,
    load_outputs,
    outputs_exists,
    save_image_uri,
    save_outputs,
)

REGION = DEFAULT_REGION
DOCKERFILE = REPO_ROOT / "Dockerfile"
_ABORT_IF_EXISTS_ENV = "OPENSRE_DEPLOY_ABORT_IF_EXISTS"
_IMAGE_URI_ENV = "OPENSRE_IMAGE_URI"
_PURGE_ECR_ENV = "OPENSRE_DESTROY_PURGE_ECR"

_EXTRA_ENV_KEYS_ENV = "OPENSRE_DEPLOY_EXTRA_ENV_KEYS"

_CONTAINER_ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USERS",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_ALLOWED_USERS",
    "SLACK_ALLOW_OPEN_WORKSPACE",
    "SLACK_WEBHOOK_URL",
    "LLM_PROVIDER",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
)


def _extra_env_keys() -> tuple[str, ...]:
    """Additional env keys to ship, from OPENSRE_DEPLOY_EXTRA_ENV_KEYS (CSV).

    Lets a deployment carry integration credentials (Grafana, Datadog, …) so
    the deployed agent's tools become available, without hardcoding every
    vendor's variables here.
    """
    raw = os.getenv(_EXTRA_ENV_KEYS_ENV, "")
    return tuple(key.strip() for key in raw.split(",") if key.strip())


def _collect_deploy_env_vars() -> dict[str, str]:
    env_vars: dict[str, str] = {}
    for key in (*_CONTAINER_ENV_KEYS, *_extra_env_keys()):
        val = os.getenv(key)
        if val:
            env_vars[key] = val
    return env_vars


def _abort_if_exists_enabled() -> bool:
    return os.getenv(_ABORT_IF_EXISTS_ENV, "").strip().lower() in {"1", "true", "yes"}


def _purge_ecr_enabled() -> bool:
    return os.getenv(_PURGE_ECR_ENV, "").strip().lower() in {"1", "true", "yes"}


def _resolve_image_uri() -> str:
    """Return the Docker image URI for the current deploy.

    Resolution order:
      1. ``OPENSRE_IMAGE_URI`` environment variable (explicit override).
      2. URI saved on disk by the last ``make build-image`` run.

    Raises:
        RuntimeError: When neither source is available, so the caller fails
            fast with a clear message rather than silently building in-band.
    """
    uri = os.getenv(_IMAGE_URI_ENV, "").strip()
    if uri:
        return uri
    if image_uri_exists():
        return load_image_uri()
    raise RuntimeError(
        "No pre-built image found. Run `make build-image` first to build and push the "
        f"Docker image, or set the {_IMAGE_URI_ENV} environment variable to an existing "
        "ECR image URI.\n\n"
        "  Quick start:\n"
        "    make build-image   # build once, saves URI locally\n"
        "    make deploy        # reuse the saved URI (fast)\n\n"
        "  Or in one step:\n"
        f"    {_IMAGE_URI_ENV}=<uri> make deploy"
    )


def build_image() -> str:
    """Build the Docker image and push it to ECR.

    Saves the resulting image URI locally so subsequent ``make deploy`` calls
    can reuse it without rebuilding. Run this once per code change, then call
    ``make deploy`` as many times as needed.

    Returns:
        The full ECR image URI (e.g. ``123….dkr.ecr.us-east-1.amazonaws.com/opensre:latest``).
    """
    stack = get_stack()
    start_time = time.time()
    print("=" * 60)
    print(f"Building and pushing image for {stack.stack_name}")
    print("=" * 60)
    print()

    print("Creating ECR repository (if needed)...")
    repo = ecr.create_repository(stack.ecr_repo_name, stack.stack_name, REGION)
    print(f"  - Repository: {repo['uri']}")

    print("Building and pushing Docker image...")
    image_uri = ecr.build_and_push(
        dockerfile_path=DOCKERFILE,
        repository_uri=repo["uri"],
        tag=ECR_DEFAULT_IMAGE_TAG,
        platform=ECR_DOCKER_PLATFORM,
        context_dir=REPO_ROOT,
        region=REGION,
    )
    save_image_uri(image_uri)

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Image built and pushed in {elapsed:.1f}s")
    print(f"  URI: {image_uri}")
    print("  URI saved — run `make deploy` to launch an instance with this image.")
    print("=" * 60)
    print()
    return image_uri


def cleanup_existing_deployment(*, region: str = DEFAULT_REGION) -> bool:
    """Destroy a prior deployment when outputs or stack-tagged instances exist.

    Terminates all active stack instances first so orphaned instances from a
    prior redeploy do not block security-group cleanup.

    Returns True when cleanup ran.
    """
    stack = get_stack()
    has_outputs = outputs_exists()
    instance_ids = find_stack_instance_ids(stack.stack_name, region=region)

    if not has_outputs and not instance_ids:
        return False

    if _abort_if_exists_enabled():
        raise RuntimeError(
            "Existing deployment detected "
            f"(outputs file and/or {len(instance_ids)} active instance(s)). "
            "Run `make destroy` first, or unset OPENSRE_DEPLOY_ABORT_IF_EXISTS."
        )

    print("=" * 60)
    print("Existing deployment detected — destroying previous stack")
    if instance_ids:
        print(f"  Active instances: {', '.join(instance_ids)}")
    if has_outputs:
        print("  Outputs file: present")
    print("=" * 60)
    print()

    for instance_id in instance_ids:
        print(f"Terminating stack instance {instance_id}...")
        terminate_instance(instance_id, region)

    if has_outputs:
        destroy()
    elif instance_ids:
        print("No outputs file — skipped IAM cleanup.")

    print()
    return True


def deploy() -> dict[str, str]:
    """Launch an EC2 instance and wait for web + gateway containers to become healthy.

    Requires a pre-built ECR image. Run ``make build-image`` first, or set the
    ``OPENSRE_IMAGE_URI`` environment variable to an existing image URI.
    """
    validate_deploy_env()

    stack = get_stack()
    start_time = time.time()

    image_uri = _resolve_image_uri()

    print("=" * 60)
    print(f"Deploying {stack.stack_name} (web + gateway containers on one EC2 instance)")
    print("=" * 60)
    print()
    print(f"Using image: {image_uri}")
    print()

    cleanup_existing_deployment(region=REGION)

    print("Creating IAM instance profile...")
    profile_info = create_instance_profile(
        role_name=f"{stack.stack_name}-role",
        profile_name=f"{stack.stack_name}-profile",
        stack_name=stack.stack_name,
        region=REGION,
        extra_policy_arns=[SSM_MANAGED_POLICY_ARN],
    )
    print(f"  - Profile: {profile_info['ProfileName']}")

    print("Looking up latest Amazon Linux 2023 AMI...")
    ami_id = get_latest_al2023_ami(REGION)
    print(f"  - AMI: {ami_id}")

    print("Creating security group...")
    security_group_id = create_stack_security_group(stack.stack_name, region=REGION)
    print(f"  - Security group: {security_group_id}")

    print("Launching EC2 instance...")
    instance = launch_instance(
        ami_id=ami_id,
        instance_profile_arn=profile_info["ProfileArn"],
        stack_name=stack.stack_name,
        instance_type=INSTANCE_TYPE,
        security_group_ids=[security_group_id],
        region=REGION,
    )
    print(f"  - Instance ID: {instance['InstanceId']}")

    print("Waiting for instance to start...")
    running = wait_for_running(instance["InstanceId"], REGION)
    public_ip = running["PublicIpAddress"]
    print(f"  - Public IP: {public_ip}")

    print("Waiting for SSM agent to register...")
    wait_for_ssm_registration(instance["InstanceId"], REGION)
    print("  - SSM: Online")

    print("Provisioning instance via SSM (Docker install, image pull, containers)...")
    provision_instance_via_ssm(
        instance["InstanceId"],
        image_uri=image_uri,
        container_env_vars=_collect_deploy_env_vars(),
        region=REGION,
    )
    print("  - Provision: OK")

    print("Waiting for web and gateway containers (may take several minutes)...")
    wait_for_deployment_ready(
        instance_id=instance["InstanceId"],
        region=REGION,
    )

    outputs = {
        "StackName": stack.stack_name,
        "InstanceId": instance["InstanceId"],
        "PublicIpAddress": public_ip,
        "SecurityGroupId": security_group_id,
        "ProfileName": profile_info["ProfileName"],
        "RoleName": profile_info["RoleName"],
        "AmiId": ami_id,
        "ImageUri": image_uri,
        "WebContainer": stack.web_container_name,
        "GatewayContainer": stack.gateway_container_name,
    }

    save_outputs(outputs)

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Deployment completed in {elapsed:.1f}s")
    print("=" * 60)
    print()
    for key, value in outputs.items():
        print(f"  {key}: {value}")

    return outputs


def destroy() -> dict[str, list[str]]:
    """Terminate the EC2 instance and clean up EC2/IAM resources.

    The ECR repository and its images are kept by default so that a
    subsequent ``make deploy`` does not need a full ``make build-image``
    rebuild — this is the main lever for keeping repeated deploy/destroy
    cycles fast and cheap. Set ``OPENSRE_DESTROY_PURGE_ECR=1`` to also
    delete the ECR repository (e.g. for a full account cleanup).
    """
    stack = get_stack()
    start_time = time.time()
    print("=" * 60)
    print(f"Destroying {stack.stack_name} infrastructure")
    print("=" * 60)
    print()

    results: dict[str, list[str]] = {"deleted": [], "failed": []}

    try:
        outputs = load_outputs()
    except FileNotFoundError:
        print("No outputs file found — attempting cleanup by known names.")
        outputs = {}

    instance_id = outputs.get("InstanceId", "")
    security_group_id = outputs.get("SecurityGroupId", "")
    profile_name = outputs.get("ProfileName", f"{stack.stack_name}-profile")
    role_name = outputs.get("RoleName", f"{stack.stack_name}-role")

    if instance_id:
        print(f"Terminating EC2 instance {instance_id}...")
        try:
            terminate_instance(instance_id, DEFAULT_REGION)
            results["deleted"].append(f"ec2-instance:{instance_id}")
            print("  - Instance terminated")
        except ClientError as e:
            msg = f"ec2-instance:{instance_id} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")

    if security_group_id:
        print(f"Deleting security group {security_group_id}...")
        try:
            delete_stack_security_group(security_group_id, region=DEFAULT_REGION)
            results["deleted"].append(f"security-group:{security_group_id}")
            print("  - Security group deleted")
        except ClientError as e:
            msg = f"security-group:{security_group_id} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")

    print(f"Deleting IAM profile {profile_name} and role {role_name}...")
    try:
        delete_instance_profile(profile_name, role_name, DEFAULT_REGION)
        results["deleted"].append(f"instance-profile:{profile_name}")
        results["deleted"].append(f"iam-role:{role_name}")
        print("  - Profile and role deleted")
    except ClientError as e:
        msg = f"iam:{profile_name}/{role_name} - {e}"
        results["failed"].append(msg)
        print(f"  - Failed: {e}")

    if _purge_ecr_enabled():
        print(f"Deleting ECR repository {stack.ecr_repo_name} ({_PURGE_ECR_ENV}=1)...")
        try:
            ecr.delete_repository(stack.ecr_repo_name, DEFAULT_REGION)
            results["deleted"].append(f"ecr-repository:{stack.ecr_repo_name}")
            print("  - ECR repository deleted")
        except ClientError as e:
            msg = f"ecr-repository:{stack.ecr_repo_name} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")
    else:
        print(
            f"Keeping ECR repository {stack.ecr_repo_name} "
            f"(set {_PURGE_ECR_ENV}=1 to also delete it)."
        )

    delete_outputs()

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Destroy completed in {elapsed:.1f}s")
    print("=" * 60)

    if results["deleted"]:
        print(f"\nDeleted {len(results['deleted'])} resources:")
        for r in results["deleted"]:
            print(f"  - {r}")

    if results["failed"]:
        print(f"\nFailed to delete {len(results['failed'])} resources:")
        for r in results["failed"]:
            print(f"  - {r}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenSRE EC2 deployment lifecycle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("build-image", help="Build and push the Docker image to ECR")
    subparsers.add_parser("deploy", help="Launch EC2 instance using a pre-built image")
    subparsers.add_parser("destroy", help="Tear down the EC2 stack")
    args = parser.parse_args()

    if args.command == "build-image":
        build_image()
    elif args.command == "deploy":
        deploy()
    else:
        destroy()


if __name__ == "__main__":
    run_lifecycle_main(main)
