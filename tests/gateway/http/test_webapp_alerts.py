"""HTTP-surface tests for ``POST /alerts`` error handling.

A rejected alert payload must return only the exception *type*, never the
underlying detail (submitted values, required-field names, or the pydantic
model name), which stays in the server log.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gateway.http.webapp import app

_TOKEN = "test-listener-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENSRE_ALERT_LISTENER_TOKEN", _TOKEN)
    return TestClient(app)


def test_non_object_payload_returns_generic_type_only(client: TestClient) -> None:
    # Arrange: a JSON array is not the object the endpoint expects.
    payload = ["not", "an", "object"]

    # Act
    response = client.post("/alerts", json=payload, headers=_AUTH)

    # Assert: caller sees the exception type and nothing else.
    assert response.status_code == 400
    assert response.json() == {"error": "invalid alert payload: TypeError"}


def test_validation_failure_does_not_leak_detail(client: TestClient) -> None:
    # Arrange: omit the required ``text`` field, and give another field a
    # distinctive value that pydantic would echo verbatim (``input_value=...``)
    # inside the raw ValidationError message.
    payload = {"severity": "SENSITIVE-DO-NOT-LEAK"}

    # Act
    response = client.post("/alerts", json=payload, headers=_AUTH)

    # Assert: the response is the generic type only...
    assert response.status_code == 400
    assert response.json() == {"error": "invalid alert payload: ValidationError"}

    # ...and none of the exception's internal detail reaches the caller.
    body = response.text
    assert "SENSITIVE-DO-NOT-LEAK" not in body  # submitted value not echoed
    assert "IncomingAlert" not in body  # model name not exposed
    assert "Field required" not in body  # pydantic error text not dumped
