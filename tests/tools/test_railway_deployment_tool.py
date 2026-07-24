from __future__ import annotations

from integrations.railway.tools.railway_deployment_tool.inspect_tool import (
    inspect_railway_deployment,
)
from integrations.railway.tools.railway_deployment_tool.redeploy_tool import (
    redeploy_railway_service,
)


def test_generic_railway_tools_have_service_level_names() -> None:
    assert inspect_railway_deployment.name == "inspect_railway_deployment"
    assert redeploy_railway_service.name == "redeploy_railway_service"
    assert "Slack bot" not in inspect_railway_deployment.description
    assert "Slack bot" not in redeploy_railway_service.description


def test_generic_redeploy_still_requires_confirmation() -> None:
    result = redeploy_railway_service.run()

    assert result["status"] == "failed"
    assert result["error_type"] == "confirmation_required"
