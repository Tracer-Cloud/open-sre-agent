"""Incident.io REST API client.

Wraps the incident.io API endpoints used for alert investigation and triage.
Credentials come from the user's incident.io integration stored locally or via env vars.
"""

from __future__ import annotations

from app.services.incident_io.client import (
    IncidentIoClient,
    make_incident_io_client,
)

__all__ = [
    "IncidentIoClient",
    "make_incident_io_client",
]
