from __future__ import annotations

import os

import pytest

from app.services.incident_io import make_incident_io_client

pytestmark = pytest.mark.skipif(
    not os.environ.get("INCIDENT_IO_API_KEY"),
    reason="INCIDENT_IO_API_KEY not set",
)


def test_incident_io_real_api_list_and_optional_context() -> None:
    client = make_incident_io_client(
        os.environ.get("INCIDENT_IO_API_KEY"),
        base_url=os.environ.get("INCIDENT_IO_BASE_URL", ""),
    )
    assert client is not None

    with client:
        listed = client.list_incidents(status_category="", page_size=1)
        assert listed["success"] is True
        assert "incidents" in listed

        incident_id = os.environ.get("INCIDENT_IO_TEST_INCIDENT_ID", "")
        if incident_id:
            context = client.get_incident_context(incident_id, update_limit=5)
            assert context["success"] is True
            assert context["incident"]["id"] == incident_id
            assert "incident_updates" in context


@pytest.mark.skipif(
    os.environ.get("INCIDENT_IO_E2E_WRITEBACK") != "1"
    or not os.environ.get("INCIDENT_IO_WRITEBACK_TEST_INCIDENT_ID"),
    reason="set INCIDENT_IO_E2E_WRITEBACK=1 and INCIDENT_IO_WRITEBACK_TEST_INCIDENT_ID",
)
def test_incident_io_real_api_summary_writeback() -> None:
    client = make_incident_io_client(
        os.environ.get("INCIDENT_IO_API_KEY"),
        base_url=os.environ.get("INCIDENT_IO_BASE_URL", ""),
    )
    assert client is not None

    with client:
        result = client.append_summary_update(
            os.environ["INCIDENT_IO_WRITEBACK_TEST_INCIDENT_ID"],
            title="OpenSRE e2e verification",
            body="Automated sandbox write-back verification.",
            notify_incident_channel=False,
        )
        assert result["success"] is True
