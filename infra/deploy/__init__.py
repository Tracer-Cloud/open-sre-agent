"""EC2 deployment for OpenSRE web and gateway runtimes."""

from infra.deploy.deploy import deploy
from infra.deploy.destroy import destroy

__all__ = ["deploy", "destroy"]
