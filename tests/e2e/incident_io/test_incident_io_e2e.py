"""Real-API gated E2E tests for incident.io."""

import os
import pytest
from app.services.incident_io.client import make_incident_io_client


@pytest.mark.skipif(not os.environ.get("INCIDENT_IO_API_KEY"), reason="INCIDENT_IO_API_KEY not set")
def test_incident_io_real_api_connectivity():
    """Verify connectivity against a real Incident.io account if API key is provided."""
    api_key = os.environ["INCIDENT_IO_API_KEY"]
    region = os.environ.get("INCIDENT_IO_REGION", "us")

    client = make_incident_io_client(api_key, region)
    assert client is not None

    with client:
        result = client.list_incidents(status="", page_size=1)
        assert result["success"] is True
        assert "incidents" in result
