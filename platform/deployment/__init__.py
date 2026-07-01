"""EC2 deployment: web and gateway containers on a single instance."""

from platform.deployment.lifecycle import deploy, destroy

__all__ = ["deploy", "destroy"]
