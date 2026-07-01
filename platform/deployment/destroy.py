#!/usr/bin/env python3
"""Destroy an OpenSRE EC2 deployment and clean up resources."""

from __future__ import annotations

import time

from botocore.exceptions import ClientError

from platform.deployment.aws.client import DEFAULT_REGION
from platform.deployment.aws.ec2 import delete_instance_profile, terminate_instance
from platform.deployment.aws.vpc import delete_security_group
from platform.deployment.modes import get_stack
from platform.deployment.outputs import delete_outputs, load_outputs


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


if __name__ == "__main__":
    destroy()
