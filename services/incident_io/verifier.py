"""incident.io integration verifier."""

from __future__ import annotations

from integrations.config_models import IncidentIoIntegrationConfig
from integrations.verification import register_probe_verifier
from services.incident_io import IncidentIoClient

verify_incident_io = register_probe_verifier(
    "incident_io",
    config=IncidentIoIntegrationConfig.model_validate,
    client=IncidentIoClient,
)
