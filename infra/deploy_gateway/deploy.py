#!/usr/bin/env python3
"""Deploy the Telegram Gateway container on an EC2 instance.

Creates:
- 1 ECR repository (opensre-gateway) and builds/pushes gateway/Dockerfile
- 1 IAM role + instance profile for EC2 (with SSM managed-instance policy)
- 1 Security group with no inbound rules (gateway uses outbound long-poll only)
- 1 EC2 instance running the gateway Docker container
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from infra.deploy_gateway import ecr
from infra.deploy_gateway.aws_client import DEFAULT_REGION
from infra.deploy_gateway.ec2 import (
    INSTANCE_TYPE,
    create_instance_profile,
    get_latest_al2023_ami,
    launch_instance,
    wait_for_running,
)
from infra.deploy_gateway.instance import (
    GATEWAY_ECR_REPO_NAME,
    SSM_MANAGED_POLICY_ARN,
    generate_gateway_user_data,
    wait_for_gateway_process,
    wait_for_ssm_registration,
)
from infra.deploy_gateway.outputs import STACK_NAME, save_outputs
from infra.deploy_gateway.vpc import (
    create_security_group,
    get_default_vpc,
    get_public_subnets,
)

REGION = DEFAULT_REGION

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DOCKERFILE = REPO_ROOT / "gateway" / "Dockerfile"


def deploy() -> dict[str, str]:
    """Build the gateway image, push to ECR, launch EC2, and wait for the gateway process.

    Required env vars forwarded into the container:
        TELEGRAM_BOT_TOKEN      - BotFather bot token
        TELEGRAM_ALLOWED_USERS  - comma-separated user IDs allowed to DM the bot
        LLM_PROVIDER            - openai | anthropic | bedrock
        OPENAI_API_KEY / ANTHROPIC_API_KEY - matching LLM key

    Returns:
        Dict of output values saved to the stack outputs file.
    """
    start_time = time.time()
    print("=" * 60)
    print(f"Deploying {STACK_NAME} infrastructure")
    print("=" * 60)
    print()

    print("Building and pushing gateway image to ECR...")
    repo = ecr.create_repository(GATEWAY_ECR_REPO_NAME, STACK_NAME, REGION)
    image_uri = ecr.build_and_push(
        dockerfile_path=GATEWAY_DOCKERFILE,
        repository_uri=repo["uri"],
        tag="latest",
        platform="linux/amd64",
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
        name=f"{STACK_NAME}-sg",
        vpc_id=vpc["vpc_id"],
        description="Gateway EC2: outbound-only (no inbound rules required)",
        ingress_rules=[],
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  - Security group: {sg['group_id']}")

    print("Creating IAM instance profile...")
    profile = create_instance_profile(
        role_name=f"{STACK_NAME}-role",
        profile_name=f"{STACK_NAME}-profile",
        stack_name=STACK_NAME,
        region=REGION,
        extra_policy_arns=[SSM_MANAGED_POLICY_ARN],
    )
    print(f"  - Profile: {profile['ProfileName']}")

    print("Looking up latest Amazon Linux 2023 AMI...")
    ami_id = get_latest_al2023_ami(REGION)
    print(f"  - AMI: {ami_id}")

    env_vars: dict[str, str] = {}
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ALLOWED_USERS",
        "LLM_PROVIDER",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
    ):
        val = os.getenv(key)
        if val:
            env_vars[key] = val

    if not env_vars.get("TELEGRAM_BOT_TOKEN"):
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Export it before running the gateway deployment."
        )

    user_data = generate_gateway_user_data(image_uri=image_uri, env_vars=env_vars)

    print("Launching EC2 instance...")
    instance = launch_instance(
        ami_id=ami_id,
        subnet_id=subnet_id,
        security_group_id=sg["group_id"],
        instance_profile_arn=profile["ProfileArn"],
        user_data=user_data,
        stack_name=STACK_NAME,
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

    print("Waiting for gateway process (may take 5-10 minutes)...")
    wait_for_gateway_process(instance["InstanceId"], REGION)
    print("  - Gateway: OK")

    outputs = {
        "InstanceId": instance["InstanceId"],
        "PublicIpAddress": public_ip,
        "SecurityGroupId": sg["group_id"],
        "ProfileName": profile["ProfileName"],
        "RoleName": profile["RoleName"],
        "AmiId": ami_id,
        "SubnetId": subnet_id,
        "VpcId": vpc["vpc_id"],
        "ImageUri": image_uri,
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


if __name__ == "__main__":
    deploy()
