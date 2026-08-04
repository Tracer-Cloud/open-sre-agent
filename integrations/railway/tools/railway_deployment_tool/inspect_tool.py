from __future__ import annotations

from typing import Any, ClassVar

from core.domain.types.evidence import EvidenceSource
from core.tool_framework.base import BaseTool
from core.tool_framework.tool_decorator import tool
from integrations.config_models import RailwayIntegrationConfig
from integrations.railway.client import (
    RailwayClient,
    RailwayOperationError,
    railway_config_from_sources,
)


class InspectRailwayDeploymentTool(BaseTool):
    name = "inspect_railway_deployment"
    source: ClassVar[EvidenceSource] = "railway"
    surfaces = ("investigation", "chat", "action")
    side_effect_level = "read_only"
    description = (
        "Show the latest successful Railway deployment and source commit metadata for a service."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Railway project ID or name."},
            "service": {"type": "string", "description": "Railway service ID or name."},
            "environment": {"type": "string", "description": "Railway environment name or ID."},
        },
        "additionalProperties": False,
    }
    outputs = {
        "deployment": "Latest successful deployment and source metadata.",
        "error": "Failure detail.",
    }

    def is_available(self, sources: dict[str, dict[object, object]]) -> bool:
        return RailwayClient(railway_config_from_sources(sources)).is_available

    def extract_params(self, sources: dict[str, dict[object, object]]) -> dict[str, object]:
        return {"railway_config": railway_config_from_sources(sources)}

    def run(
        self,
        project: str = "",
        service: str = "",
        environment: str = "",
        railway_config: RailwayIntegrationConfig | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = RailwayClient(railway_config or RailwayIntegrationConfig())
        try:
            scope = client.resolve_scope_object(project, service, environment)
            deployment = client.inspect_deployment(scope)
        except RailwayOperationError as exc:
            return {"status": "failed", "error": str(exc), "error_type": exc.error_type}
        return {
            "status": "ok",
            "deployment": {
                "project": scope.project,
                "service": scope.service,
                "environment": scope.environment,
                "deployment_id": deployment.deployment_id,
                "status": deployment.status,
                "commit_hash": deployment.commit_hash,
                "commit_message": deployment.commit_message,
            },
        }


inspect_railway_deployment = InspectRailwayDeploymentTool()
tool(inspect_railway_deployment, surfaces=("investigation", "chat", "action"))
