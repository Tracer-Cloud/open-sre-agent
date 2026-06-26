"""Low-level HTTP GET for Supabase project APIs (PostgREST, Auth, Storage)."""

from __future__ import annotations

from typing import Any


def supabase_http_get(
    base_url: str,
    path: str,
    headers: dict[str, str],
    *,
    timeout_seconds: float,
    params: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """GET ``path`` under ``base_url``; return (status_code, JSON body or raw text)."""
    import httpx  # type: ignore[import-untyped]

    url = f"{base_url.rstrip('/')}{path}"
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.get(url, headers=headers, params=params or {})
    try:
        body: Any = response.json()
    except Exception:
        body = response.text
    return response.status_code, body
