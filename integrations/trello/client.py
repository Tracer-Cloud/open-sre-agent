"""Trello HTTP transport and board operations."""

from typing import Any

import httpx

from integrations.trello.config import TrelloConfig


def _request_json(
    config: TrelloConfig,
    method: str,
    path: str,
    *,
    params: list[tuple[str, str | int | float | bool | None]] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    request_params: list[tuple[str, str | int | float | bool | None]] = [
        ("key", config.api_key),
        ("token", config.token),
    ]
    if params:
        request_params.extend(params)

    url = f"{config.api_base_url}{path}"
    response = httpx.request(
        method,
        url,
        params=request_params,
        json=json,
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def validate_trello_connection(
    *,
    config: TrelloConfig,
) -> dict[str, Any]:
    """Validate Trello connection with a lightweight member query."""
    payload = _request_json(config, "GET", "/members/me")
    return payload if isinstance(payload, dict) else {}


def create_trello_card(
    *,
    config: TrelloConfig,
    name: str,
    desc: str,
    list_id: str | None = None,
) -> dict[str, Any]:
    """Create a Trello card."""
    target_list_id = (list_id or config.list_id).strip()
    if not target_list_id:
        raise ValueError("A list_id must be provided either via argument or config.")

    payload = _request_json(
        config,
        "POST",
        "/cards",
        params=[
            ("idList", target_list_id),
        ],
        json={
            "name": name,
            "desc": desc,
        },
    )
    return payload if isinstance(payload, dict) else {}
