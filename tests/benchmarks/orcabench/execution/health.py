"""Readiness checks that observe infrastructure without producing investigation evidence."""

from __future__ import annotations

import base64
import json
from http import HTTPStatus
from typing import Any
from urllib.request import Request, urlopen

from tests.benchmarks.orcabench.config import GrafanaSettings


def check_grafana(endpoint: str, settings: GrafanaSettings, timeout_seconds: int) -> dict[str, Any]:
    """Call Grafana's real health endpoint using the ORCA basic-auth contract."""
    token = base64.b64encode(f"{settings.username}:{settings.password}".encode()).decode()
    request = Request(
        f"{endpoint.rstrip('/')}/api/health",
        headers={"Authorization": f"Basic {token}"},
        method="GET",
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - validated URL
        if response.status != HTTPStatus.OK:
            raise RuntimeError(f"Grafana health returned HTTP {response.status}")
        body = response.read()
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise RuntimeError("Grafana health response must be a JSON object")
    return {
        "status": "ready",
        "database": payload.get("database"),
        "version": payload.get("version"),
    }
