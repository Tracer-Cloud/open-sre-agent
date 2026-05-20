#!/usr/bin/env python3
"""Backward-compatible shim for the managed remote EC2 deploy method."""

from __future__ import annotations

from app.deployment.methods.ec2_remote import deploy_remote

STACK_NAME = "tracer-ec2-remote"


def deploy(branch: str = "main") -> dict[str, str]:
    return deploy_remote(stack_name=STACK_NAME, branch=branch)


if __name__ == "__main__":
    deploy()
