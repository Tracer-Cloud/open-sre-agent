#!/usr/bin/env python3
"""Backward-compatible shim for managed remote EC2 destroy."""

from __future__ import annotations

from app.deployment.methods.ec2_remote import destroy_remote

STACK_NAME = "tracer-ec2-remote"


def destroy() -> dict[str, list[str]]:
    return destroy_remote(stack_name=STACK_NAME)


if __name__ == "__main__":
    destroy()
