"""PostHog HTTP transport helpers."""

from __future__ import annotations

from typing import Any

import httpx

from integrations.posthog.config import PostHogConfig


def request_json(
    config: PostHogConfig,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    url = f"{config.api_base_url}{path}"
    response = httpx.request(
        method,
        url,
        headers=config.auth_headers,
        params=params,
        json=json,
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


# Back-compat alias for verifier/tests that patch the private name.
_request_json = request_json
