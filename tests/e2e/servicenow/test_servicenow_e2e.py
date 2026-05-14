from __future__ import annotations

import os

import pytest

from app.services.servicenow import make_servicenow_client


@pytest.mark.e2e
def test_servicenow_real_api_list_incidents() -> None:
    instance_url = os.getenv("SERVICENOW_INSTANCE_URL", "").strip()
    api_token = os.getenv("SERVICENOW_API_TOKEN", "").strip()
    username = os.getenv("SERVICENOW_USERNAME", "").strip()
    password = os.getenv("SERVICENOW_PASSWORD", "").strip()
    if not instance_url or not (api_token or (username and password)):
        pytest.skip("Set SERVICENOW_INSTANCE_URL and ServiceNow credentials to run this e2e test.")

    client = make_servicenow_client(
        instance_url,
        username=username,
        password=password,
        api_token=api_token,
    )
    assert client is not None
    with client:
        result = client.list_incidents(limit=1)

    assert result["success"] is True
    assert "incidents" in result
