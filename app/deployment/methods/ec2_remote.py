"""Provision and destroy the managed remote OpenSRE EC2 deployment."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from app.deployment.operations.ec2_config import (
    delete_remote_outputs,
    load_remote_outputs,
    save_remote_outputs,
)
from tests.deployment.ec2.infrastructure_sdk.instance import (
    create_instance_profile,
    delete_instance_profile,
    launch_instance,
    terminate_instance,
    wait_for_running,
)
from tests.deployment.ec2.infrastructure_sdk.remote_instance import (
    INSTANCE_TYPE,
    SERVER_PORT,
    generate_remote_user_data,
    get_latest_al2023_ami,
    wait_for_remote_health,
)
from tests.shared.infrastructure_sdk.deployer import DEFAULT_REGION
from tests.shared.infrastructure_sdk.resources.vpc import (
    create_security_group,
    delete_security_group,
    get_default_vpc,
    get_public_subnets,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DOTENV = _PROJECT_ROOT / ".env"
_DEFAULT_STACK_NAME = "tracer-ec2-remote"


def _load_env_vars(dotenv_path: Path) -> dict[str, str]:
    env_vars: dict[str, str] = {}
    if dotenv_path.is_file():
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            cleaned = value.strip().strip("'\"")
            if key:
                env_vars[key] = cleaned
    return env_vars


def deploy_remote(
    *,
    stack_name: str = _DEFAULT_STACK_NAME,
    branch: str = "main",
    region: str = DEFAULT_REGION,
    dotenv_path: Path | None = None,
) -> dict[str, str]:
    """Deploy a full remote investigation server on EC2 and persist outputs."""
    start_time = time.time()
    print("=" * 60)
    print(f"Deploying {stack_name}")
    print("=" * 60)
    print()

    vpc = get_default_vpc(region)
    subnets = get_public_subnets(vpc["vpc_id"], region)
    subnet_id = subnets[0]

    sg = create_security_group(
        name=f"{stack_name}-sg",
        vpc_id=vpc["vpc_id"],
        description="OpenSRE remote server - SSH and investigation API",
        ingress_rules=[
            {"port": 22, "cidr": "0.0.0.0/0", "description": "SSH"},
            {"port": SERVER_PORT, "cidr": "0.0.0.0/0", "description": "Investigation API"},
        ],
        stack_name=stack_name,
        region=region,
    )
    profile = create_instance_profile(
        role_name=f"{stack_name}-role",
        profile_name=f"{stack_name}-profile",
        stack_name=stack_name,
        region=region,
    )
    ami_id = get_latest_al2023_ami(region)

    env_vars = _load_env_vars(dotenv_path or _DEFAULT_DOTENV)
    env_vars["LLM_PROVIDER"] = "bedrock"
    env_vars["AWS_REGION"] = region
    env_vars.pop("OLLAMA_MODEL", None)
    env_vars.pop("OLLAMA_HOST", None)
    user_data = generate_remote_user_data(env_vars=env_vars, branch=branch)

    instance = launch_instance(
        ami_id=ami_id,
        subnet_id=subnet_id,
        security_group_id=sg["group_id"],
        instance_profile_arn=profile["ProfileArn"],
        user_data=user_data,
        stack_name=stack_name,
        instance_type=INSTANCE_TYPE,
        region=region,
    )
    running = wait_for_running(instance["InstanceId"], region)
    public_ip = running["PublicIpAddress"]
    wait_for_remote_health(public_ip)

    outputs = {
        "StackName": stack_name,
        "InstanceId": instance["InstanceId"],
        "PublicIpAddress": public_ip,
        "SecurityGroupId": sg["group_id"],
        "ProfileName": profile["ProfileName"],
        "RoleName": profile["RoleName"],
        "AmiId": ami_id,
        "SubnetId": subnet_id,
        "VpcId": vpc["vpc_id"],
        "ServerPort": str(SERVER_PORT),
    }
    save_remote_outputs(outputs)

    elapsed = time.time() - start_time
    print(f"Deployment completed in {elapsed:.1f}s")
    return outputs


def destroy_remote(
    *,
    stack_name: str = _DEFAULT_STACK_NAME,
    region: str = DEFAULT_REGION,
) -> dict[str, list[str]]:
    """Destroy the managed remote EC2 deployment and delete saved outputs."""
    start_time = time.time()
    results: dict[str, list[str]] = {"deleted": [], "failed": []}

    try:
        outputs: dict[str, Any] = load_remote_outputs()
    except FileNotFoundError:
        outputs = {}

    instance_id = str(outputs.get("InstanceId", "")).strip()
    sg_id = str(outputs.get("SecurityGroupId", "")).strip()
    profile_name = str(outputs.get("ProfileName", "")).strip() or f"{stack_name}-profile"
    role_name = str(outputs.get("RoleName", "")).strip() or f"{stack_name}-role"

    if instance_id:
        try:
            terminate_instance(instance_id, region)
            results["deleted"].append(f"ec2-instance:{instance_id}")
        except ClientError as exc:
            results["failed"].append(f"ec2-instance:{instance_id} - {exc}")

    if sg_id:
        try:
            time.sleep(10)
            delete_security_group(sg_id, region)
            results["deleted"].append(f"security-group:{sg_id}")
        except ClientError as exc:
            results["failed"].append(f"security-group:{sg_id} - {exc}")

    try:
        delete_instance_profile(profile_name, role_name, region)
        results["deleted"].append(f"instance-profile:{profile_name}")
        results["deleted"].append(f"iam-role:{role_name}")
    except ClientError as exc:
        results["failed"].append(f"iam:{profile_name}/{role_name} - {exc}")

    delete_remote_outputs()
    elapsed = time.time() - start_time
    print(f"Destroy completed in {elapsed:.1f}s")
    return results


__all__ = ["deploy_remote", "destroy_remote"]
