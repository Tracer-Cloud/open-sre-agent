"""Deployment methods that perform infrastructure provisioning."""

from __future__ import annotations

from app.deployment.methods.ec2_remote import deploy_remote, destroy_remote

__all__ = ["deploy_remote", "destroy_remote"]
