"""EC2 deployment for the Telegram Gateway."""

from infra.deploy_gateway.deploy import deploy
from infra.deploy_gateway.destroy import destroy

__all__ = ["deploy", "destroy"]
