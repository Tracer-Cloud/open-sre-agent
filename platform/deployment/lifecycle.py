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
    delete_instance_profile,
    find_stack_instance_ids,
    get_latest_al2023_ami,
    launch_instance,
    terminate_instance,
    wait_for_running,
)
from platform.deployment.aws.ssm import wait_for_ssm_registration
from platform.deployment.aws.vpc import (
    create_security_group,
    delete_security_group,
    get_default_vpc,
    get_public_subnets,
)
from platform.deployment.instance import (
    provision_instance_via_ssm,
    wait_for_deployment_ready,
)
from platform.deployment.prep import validate_deploy_env
from platform.deployment.stack import (
    delete_outputs,
    get_stack,
    load_outputs,
    outputs_exists,
    save_outputs,
)

REGION = DEFAULT_REGION
_ABORT_IF_EXISTS_ENV = "OPENSRE_DEPLOY_ABORT_IF_EXISTS"

_CONTAINER_ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USERS",
    "LLM_PROVIDER",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
)


def _collect_deploy_env_vars() -> dict[str, str]:
    env_vars: dict[str, str] = {}
    for key in _CONTAINER_ENV_KEYS:
        val = os.getenv(key)
        if val:
            env_vars[key] = val
    return env_vars


def _abort_if_exists_enabled() -> bool:
    return os.getenv(_ABORT_IF_EXISTS_ENV, "").strip().lower() in {"1", "true", "yes"}


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
        print("No outputs file — skipped security group and IAM cleanup.")

    print()
    return True


def deploy() -> dict[str, str]:
    """Build the image, push to ECR, launch EC2, and wait for web + gateway to become healthy."""
    validate_deploy_env()

    stack = get_stack()
    start_time = time.time()
    print("=" * 60)
    print(f"Deploying {stack.stack_name} (web + gateway containers on one EC2 instance)")
    print("=" * 60)
    print()

    cleanup_existing_deployment(region=REGION)

    print("Building and pushing image to ECR...")
    repo = ecr.create_repository(stack.ecr_repo_name, stack.stack_name, REGION)
    image_uri = ecr.build_and_push(
        dockerfile_path=REPO_ROOT / "Dockerfile",
        repository_uri=repo["uri"],
        tag=ECR_DEFAULT_IMAGE_TAG,
        platform=ECR_DOCKER_PLATFORM,
        context_dir=REPO_ROOT,
        region=REGION,
    )
    print(f"  - Image: {image_uri}")

    print("Getting VPC and subnet...")
    vpc = get_default_vpc(REGION)
    subnets = get_public_subnets(vpc["vpc_id"], REGION)
    subnet_id = subnets[0]
    print(f"  - VPC: {vpc['vpc_id']}")
    print(f"  - Subnet: {subnet_id}")

    print("Creating security group...")
    sg = create_security_group(
        name=f"{stack.stack_name}-sg",
        vpc_id=vpc["vpc_id"],
        description=stack.security_group_description,
        ingress_rules=stack.ingress_rules,
        stack_name=stack.stack_name,
        region=REGION,
    )
    print(f"  - Security group: {sg['group_id']}")

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

    print("Launching EC2 instance...")
    instance = launch_instance(
        ami_id=ami_id,
        subnet_id=subnet_id,
        security_group_id=sg["group_id"],
        instance_profile_arn=profile_info["ProfileArn"],
        stack_name=stack.stack_name,
        instance_type=INSTANCE_TYPE,
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
        public_ip=public_ip,
        region=REGION,
    )

    outputs = {
        "StackName": stack.stack_name,
        "InstanceId": instance["InstanceId"],
        "PublicIpAddress": public_ip,
        "SecurityGroupId": sg["group_id"],
        "ProfileName": profile_info["ProfileName"],
        "RoleName": profile_info["RoleName"],
        "AmiId": ami_id,
        "SubnetId": subnet_id,
        "VpcId": vpc["vpc_id"],
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
    """Terminate the EC2 instance and clean up all associated resources."""
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
    sg_id = outputs.get("SecurityGroupId", "")
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

    if sg_id:
        print(f"Deleting security group {sg_id}...")
        try:
            delete_security_group(sg_id, DEFAULT_REGION)
            results["deleted"].append(f"security-group:{sg_id}")
            print("  - Security group deleted")
        except ClientError as e:
            msg = f"security-group:{sg_id} - {e}"
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

    print(f"Deleting ECR repository {stack.ecr_repo_name}...")
    try:
        ecr.delete_repository(stack.ecr_repo_name, DEFAULT_REGION)
        results["deleted"].append(f"ecr-repository:{stack.ecr_repo_name}")
        print("  - ECR repository deleted")
    except ClientError as e:
        msg = f"ecr-repository:{stack.ecr_repo_name} - {e}"
        results["failed"].append(msg)
        print(f"  - Failed: {e}")

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
    subparsers.add_parser("deploy", help="Provision the EC2 stack")
    subparsers.add_parser("destroy", help="Tear down the EC2 stack")
    args = parser.parse_args()

    if args.command == "deploy":
        deploy()
    else:
        destroy()


if __name__ == "__main__":
    main()
