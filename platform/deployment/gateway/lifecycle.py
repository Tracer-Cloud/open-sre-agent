#!/usr/bin/env python3
"""Deploy and destroy the OpenSRE Telegram gateway on EC2 using a custom AMI."""

from __future__ import annotations

import argparse
import os
import time

from botocore.exceptions import ClientError

from platform.deployment.aws.client import DEFAULT_REGION
from platform.deployment.aws.config import (
    GATEWAY_AMI_DESTROY_PURGE_ENV,
    GATEWAY_AMI_ID_ENV,
    INSTANCE_TYPE,
    SSM_MANAGED_POLICY_ARN,
)
from platform.deployment.aws.ec2 import (
    create_instance_profile,
    delete_instance_profile,
    deregister_image,
    find_stack_instance_ids,
    launch_instance,
    terminate_instance,
    wait_for_running,
)
from platform.deployment.aws.ssm import wait_for_ssm_registration
from platform.deployment.ecr_deploy.prep import run_lifecycle_main, validate_deploy_env
from platform.deployment.gateway.bake import bake_ami
from platform.deployment.gateway.direct_deploy import deploy_direct, destroy_direct
from platform.deployment.gateway.provision import (
    provision_gateway_via_ssm,
    wait_for_gateway_ready,
)
from platform.deployment.gateway.stack import (
    ami_id_exists,
    delete_outputs,
    get_stack,
    load_ami_id,
    load_outputs,
    outputs_exists,
    save_outputs,
)

REGION = DEFAULT_REGION

_CONTAINER_ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USERS",
    "LLM_PROVIDER",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
)

_ABORT_IF_EXISTS_ENV = "OPENSRE_DEPLOY_ABORT_IF_EXISTS"


def _collect_deploy_env_vars() -> dict[str, str]:
    env_vars: dict[str, str] = {}
    for key in _CONTAINER_ENV_KEYS:
        val = os.getenv(key)
        if val:
            env_vars[key] = val
    return env_vars


def _abort_if_exists_enabled() -> bool:
    return os.getenv(_ABORT_IF_EXISTS_ENV, "").strip().lower() in {"1", "true", "yes"}


def _purge_ami_enabled() -> bool:
    return os.getenv(GATEWAY_AMI_DESTROY_PURGE_ENV, "").strip().lower() in {"1", "true", "yes"}


def _resolve_ami_id() -> str:
    """Return the AMI id to deploy.

    Resolution order:
      1. ``OPENSRE_GATEWAY_AMI_ID`` environment variable.
      2. AMI id saved on disk by the last ``make bake-gateway`` run.

    Raises:
        RuntimeError: When neither source is available.
    """
    ami_id = os.getenv(GATEWAY_AMI_ID_ENV, "").strip()
    if ami_id:
        return ami_id
    if ami_id_exists():
        return load_ami_id()
    raise RuntimeError(
        "No pre-built gateway AMI found. Run `make bake-gateway` first to build one, "
        f"or set {GATEWAY_AMI_ID_ENV} to an existing AMI id.\n\n"
        "  Quick start:\n"
        "    make bake-gateway   # bake once, saves AMI id locally\n"
        "    make deploy-gateway # launch from saved AMI (fast)\n\n"
        "  Or in one step:\n"
        f"    {GATEWAY_AMI_ID_ENV}=<ami-id> make deploy-gateway"
    )


def cleanup_existing_deployment(*, region: str = DEFAULT_REGION) -> bool:
    """Destroy a prior gateway deployment when outputs or tagged instances exist.

    Returns True when cleanup ran.
    """
    stack = get_stack()
    has_outputs = outputs_exists()
    instance_ids = find_stack_instance_ids(stack.stack_name, region=region)

    if not has_outputs and not instance_ids:
        return False

    if _abort_if_exists_enabled():
        raise RuntimeError(
            "Existing gateway deployment detected "
            f"({len(instance_ids)} active instance(s)). "
            "Run `make destroy-gateway` first, or unset OPENSRE_DEPLOY_ABORT_IF_EXISTS."
        )

    print("=" * 60)
    print("Existing gateway deployment detected — destroying previous stack")
    if instance_ids:
        print(f"  Active instances: {', '.join(instance_ids)}")
    print("=" * 60)
    print()

    for instance_id in instance_ids:
        print(f"Terminating stack instance {instance_id}...")
        terminate_instance(instance_id, region)

    if has_outputs or instance_ids:
        destroy()

    print()
    return True


def deploy() -> dict[str, str]:
    """Launch an EC2 instance from the pre-built gateway AMI and start the service."""
    validate_deploy_env()

    stack = get_stack()
    start_time = time.time()
    ami_id = _resolve_ami_id()

    print("=" * 60)
    print(f"Deploying {stack.stack_name} (gateway on one EC2 instance, systemd)")
    print("=" * 60)
    print()
    print(f"  AMI: {ami_id}")
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

    print("Launching EC2 instance from gateway AMI...")
    instance = launch_instance(
        ami_id=ami_id,
        instance_profile_arn=profile_info["ProfileArn"],
        stack_name=stack.stack_name,
        instance_type=INSTANCE_TYPE,
        region=REGION,
    )
    instance_id = instance["InstanceId"]
    print(f"  - Instance ID: {instance_id}")

    print("Waiting for instance to start...")
    running = wait_for_running(instance_id, REGION)
    public_ip = running["PublicIpAddress"]
    print(f"  - Public IP: {public_ip}")

    print("Waiting for SSM agent to register...")
    wait_for_ssm_registration(instance_id, REGION)
    print("  - SSM: Online")

    print("Provisioning gateway (writing env file, starting service)...")
    provision_gateway_via_ssm(
        instance_id,
        env_vars=_collect_deploy_env_vars(),
        region=REGION,
    )
    print("  - Provision: OK")

    print("Waiting for gateway service to become ready...")
    wait_for_gateway_ready(instance_id, region=REGION)
    print("  - Gateway: Ready")

    outputs: dict[str, str] = {
        "StackName": stack.stack_name,
        "InstanceId": instance_id,
        "PublicIpAddress": public_ip,
        "ProfileName": profile_info["ProfileName"],
        "RoleName": profile_info["RoleName"],
        "AmiId": ami_id,
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

    The custom AMI is kept by default so that a subsequent deploy does not need
    a full re-bake. Set ``OPENSRE_GATEWAY_DESTROY_PURGE_AMI=1`` to also
    deregister the AMI and delete its backing snapshot (e.g. for a full
    account cleanup).
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
    profile_name = outputs.get("ProfileName", f"{stack.stack_name}-profile")
    role_name = outputs.get("RoleName", f"{stack.stack_name}-role")
    saved_ami_id = outputs.get("AmiId", "")

    if instance_id:
        print(f"Terminating EC2 instance {instance_id}...")
        try:
            terminate_instance(instance_id, REGION)
            results["deleted"].append(f"ec2-instance:{instance_id}")
            print("  - Instance terminated")
        except ClientError as e:
            msg = f"ec2-instance:{instance_id} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")

    print(f"Deleting IAM profile {profile_name} and role {role_name}...")
    try:
        delete_instance_profile(profile_name, role_name, REGION)
        results["deleted"].append(f"instance-profile:{profile_name}")
        results["deleted"].append(f"iam-role:{role_name}")
        print("  - Profile and role deleted")
    except ClientError as e:
        msg = f"iam:{profile_name}/{role_name} - {e}"
        results["failed"].append(msg)
        print(f"  - Failed: {e}")

    if _purge_ami_enabled() and saved_ami_id:
        print(f"Deregistering gateway AMI {saved_ami_id} ({GATEWAY_AMI_DESTROY_PURGE_ENV}=1)...")
        try:
            deregister_image(saved_ami_id, region=REGION)
            results["deleted"].append(f"ami:{saved_ami_id}")
            print("  - AMI deregistered")
        except ClientError as e:
            msg = f"ami:{saved_ami_id} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")
    elif saved_ami_id:
        print(
            f"Keeping gateway AMI {saved_ami_id} "
            f"(set {GATEWAY_AMI_DESTROY_PURGE_ENV}=1 to also deregister it)."
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
    parser = argparse.ArgumentParser(description="OpenSRE gateway deployment lifecycle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bake-ami", help="Bake a gateway AMI (run once per code change)")
    subparsers.add_parser("deploy", help="Launch EC2 instance using a pre-built gateway AMI")
    subparsers.add_parser("destroy", help="Tear down the gateway AMI stack")
    subparsers.add_parser(
        "deploy-direct",
        help="Launch a fresh EC2 instance and install the gateway inline via SSM (no pre-baked AMI)",
    )
    subparsers.add_parser("destroy-direct", help="Tear down the direct-deploy gateway stack")
    args = parser.parse_args()

    if args.command == "bake-ami":
        bake_ami(region=REGION)
    elif args.command == "deploy":
        deploy()
    elif args.command == "deploy-direct":
        validate_deploy_env()
        deploy_direct(env_vars=_collect_deploy_env_vars(), region=REGION)
    elif args.command == "destroy-direct":
        destroy_direct(region=REGION)
    else:
        destroy()


if __name__ == "__main__":
    run_lifecycle_main(main)
