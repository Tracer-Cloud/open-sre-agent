#!/usr/bin/env python3
"""Deploy OpenSRE on EC2 in web or gateway mode.

Creates:
- 1 ECR repository and builds/pushes the root Dockerfile
- 1 IAM role + instance profile for EC2 (with SSM managed-instance policy)
- 1 Security group (web: inbound 8000; gateway: outbound-only)
- 1 EC2 instance running the Docker container
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from infra.aws import ecr
from infra.aws.client import DEFAULT_REGION
from infra.aws.config import (
    ECR_DEFAULT_IMAGE_TAG,
    ECR_DOCKER_PLATFORM,
    INSTANCE_TYPE,
    SSM_MANAGED_POLICY_ARN,
)
from infra.aws.ec2 import (
    create_instance_profile,
    get_latest_al2023_ami,
    launch_instance,
    wait_for_running,
)
from infra.aws.ssm import wait_for_ssm_registration
from infra.aws.vpc import create_security_group, get_default_vpc, get_public_subnets
from infra.deploy.instance import generate_user_data, wait_for_deployment_ready
from infra.deploy.modes import get_profile, resolve_deploy_mode
from infra.deploy.outputs import save_outputs

REGION = DEFAULT_REGION
REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"

_GATEWAY_ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USERS",
    "LLM_PROVIDER",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
)

_WEB_ENV_KEYS = (
    "LLM_PROVIDER",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
)


def _collect_env_vars(profile_keys: tuple[str, ...]) -> dict[str, str]:
    env_vars: dict[str, str] = {}
    for key in profile_keys:
        val = os.getenv(key)
        if val:
            env_vars[key] = val
    return env_vars


def deploy() -> dict[str, str]:
    """Build the image, push to ECR, launch EC2, and wait for the process to become healthy."""
    profile = get_profile()
    start_time = time.time()
    print("=" * 60)
    print(f"Deploying {profile.stack_name} infrastructure ({profile.mode} mode)")
    print("=" * 60)
    print()

    print(f"Building and pushing {profile.mode} image to ECR...")
    repo = ecr.create_repository(profile.ecr_repo_name, profile.stack_name, REGION)
    image_uri = ecr.build_and_push(
        dockerfile_path=DOCKERFILE,
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
        name=f"{profile.stack_name}-sg",
        vpc_id=vpc["vpc_id"],
        description=profile.security_group_description,
        ingress_rules=profile.ingress_rules,
        stack_name=profile.stack_name,
        region=REGION,
    )
    print(f"  - Security group: {sg['group_id']}")

    print("Creating IAM instance profile...")
    profile_info = create_instance_profile(
        role_name=f"{profile.stack_name}-role",
        profile_name=f"{profile.stack_name}-profile",
        stack_name=profile.stack_name,
        region=REGION,
        extra_policy_arns=[SSM_MANAGED_POLICY_ARN],
    )
    print(f"  - Profile: {profile_info['ProfileName']}")

    print("Looking up latest Amazon Linux 2023 AMI...")
    ami_id = get_latest_al2023_ami(REGION)
    print(f"  - AMI: {ami_id}")

    env_keys = _GATEWAY_ENV_KEYS if profile.mode == "gateway" else _WEB_ENV_KEYS
    env_vars = _collect_env_vars(env_keys)

    if profile.require_telegram_token and not env_vars.get("TELEGRAM_BOT_TOKEN"):
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Export it before running gateway deployment."
        )

    user_data = generate_user_data(profile=profile, image_uri=image_uri, env_vars=env_vars)

    print("Launching EC2 instance...")
    instance = launch_instance(
        ami_id=ami_id,
        subnet_id=subnet_id,
        security_group_id=sg["group_id"],
        instance_profile_arn=profile_info["ProfileArn"],
        user_data=user_data,
        stack_name=profile.stack_name,
        instance_type=INSTANCE_TYPE,
        region=REGION,
    )
    print(f"  - Instance ID: {instance['InstanceId']}")

    print("Waiting for instance to start...")
    running = wait_for_running(instance["InstanceId"], REGION)
    public_ip = running["PublicIpAddress"]
    print(f"  - Public IP: {public_ip}")

    if profile.use_ssm_health_check:
        print("Waiting for SSM agent to register...")
        wait_for_ssm_registration(instance["InstanceId"], REGION)
        print("  - SSM: Online")

    readiness_label = "gateway process" if profile.mode == "gateway" else "health endpoint"
    print(f"Waiting for {readiness_label} (may take several minutes)...")
    wait_for_deployment_ready(
        profile=profile,
        instance_id=instance["InstanceId"],
        public_ip=public_ip,
        region=REGION,
    )
    print(f"  - {profile.mode.title()}: OK")

    outputs = {
        "DeployMode": profile.mode,
        "StackName": profile.stack_name,
        "InstanceId": instance["InstanceId"],
        "PublicIpAddress": public_ip,
        "SecurityGroupId": sg["group_id"],
        "ProfileName": profile_info["ProfileName"],
        "RoleName": profile_info["RoleName"],
        "AmiId": ami_id,
        "SubnetId": subnet_id,
        "VpcId": vpc["vpc_id"],
        "ImageUri": image_uri,
    }

    save_outputs(outputs, mode=profile.mode)

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Deployment completed in {elapsed:.1f}s")
    print("=" * 60)
    print()
    for key, value in outputs.items():
        print(f"  {key}: {value}")

    return outputs


if __name__ == "__main__":
    resolve_deploy_mode()
    deploy()
