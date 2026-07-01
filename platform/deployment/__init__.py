"""EC2 deployment: web and gateway containers on a single instance."""

from platform.deployment.deploy import deploy
from platform.deployment.destroy import destroy

__all__ = ["deploy", "destroy"]
