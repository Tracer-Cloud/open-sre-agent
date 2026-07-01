"""Tests for shared integration resolution."""

from __future__ import annotations

import base64
import json
from typing import Any

import core.agent_harness.integrations.resolution as resolution


def _jwt(payload: dict[str, Any]) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    return f"{header.decode()}.{body.decode()}.sig"


def test_resolve_integrations_returns_existing_state_without_lookup(monkeypatch: Any) -> None:
    def _unexpected_lookup() -> list[dict[str, Any]]:
        raise AssertionError("local store should not be queried")

    monkeypatch.setattr("integrations.store.load_integrations", _unexpected_lookup)

    resolved = resolution.resolve_integrations(
        {"resolved_integrations": {"datadog": {"site": "datadoghq.com"}}}
    )

    assert resolved == {"datadog": {"site": "datadoghq.com"}}


def test_resolution_request_ignores_unrelated_runtime_state() -> None:
    request = resolution.IntegrationResolutionRequest.model_validate(
        {
            "_auth_token": " Bearer token ",
            "org_id": " org-123 ",
            "alert_name": "checkout failed",
            "agent_messages": [{"role": "user", "content": "hello"}],
        }
    )

    assert request.auth_token == "Bearer token"
    assert request.org_id == "org-123"
    assert not hasattr(request, "alert_name")


def test_resolution_result_rejects_unknown_fields() -> None:
    import pytest

    with pytest.raises(ValueError):
        resolution.IntegrationResolutionResult.model_validate(
            {
                "resolved_integrations": {},
                "unexpected": True,
            }
        )


def test_resolve_local_store_sources_returns_progress_metadata(monkeypatch: Any) -> None:
    store_records = [{"service": "datadog", "status": "active", "credentials": {}}]
    monkeypatch.delenv("JWT_TOKEN", raising=False)
    monkeypatch.setattr("integrations.store.load_integrations", lambda: store_records)
    monkeypatch.setattr(resolution, "_load_env_integrations", lambda: [])
    monkeypatch.setattr(
        resolution,
        "_merge_local_integrations",
        lambda store, env: [*store, *env],
    )
    monkeypatch.setattr(
        resolution,
        "_classify_integrations",
        lambda _records: {"datadog": {"site": "datadoghq.com"}},
    )

    result = resolution.resolve_integrations_with_metadata()

    assert result.resolved_integrations == {"datadog": {"site": "datadoghq.com"}}
    assert result.services == ("datadog",)
    assert result.progress_message == "Resolved local integrations from store: ['datadog']"


def test_resolution_result_is_strict_pydantic_model() -> None:
    result = resolution.IntegrationResolutionResult(
        resolved_integrations={
            "datadog": {"site": "datadoghq.com"},
            "_gateway_chat_id": "chat-1",
            "_all": [],
        },
        progress_message="Resolved",
    )

    assert result.model_dump() == {
        "resolved_integrations": {
            "datadog": {"site": "datadoghq.com"},
            "_gateway_chat_id": "chat-1",
            "_all": [],
        },
        "progress_message": "Resolved",
    }
    assert result.services == ("datadog",)


def test_resolve_env_token_merges_remote_store_and_env(monkeypatch: Any) -> None:
    remote_records = [{"service": "sentry", "status": "active", "credentials": {}}]
    store_records = [{"service": "datadog", "status": "active", "credentials": {}}]
    env_records = [{"service": "grafana", "status": "active", "credentials": {}}]
    captured_fetch: dict[str, str] = {}
    captured_merge: dict[str, list[dict[str, Any]]] = {}

    def _fetch_remote_integrations(*, org_id: str, auth_token: str) -> list[dict[str, Any]]:
        captured_fetch["org_id"] = org_id
        captured_fetch["auth_token"] = auth_token
        return remote_records

    def _merge(
        env_integrations: list[dict[str, Any]],
        store_integrations: list[dict[str, Any]],
        remote_integrations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        captured_merge["env"] = env_integrations
        captured_merge["store"] = store_integrations
        captured_merge["remote"] = remote_integrations
        return [*env_integrations, *store_integrations, *remote_integrations]

    monkeypatch.setenv("JWT_TOKEN", f"Bearer {_jwt({'organization': 'org-123'})}")
    monkeypatch.setattr("integrations.port.fetch_remote_integrations", _fetch_remote_integrations)
    monkeypatch.setattr("integrations.store.load_integrations", lambda: store_records)
    monkeypatch.setattr(resolution, "_load_env_integrations", lambda: env_records)
    monkeypatch.setattr(resolution, "_merge_integrations_by_service", _merge)
    monkeypatch.setattr(
        resolution,
        "_classify_integrations",
        lambda _records: {"datadog": {}, "grafana": {}, "sentry": {}},
    )

    result = resolution.resolve_integrations_with_metadata()

    assert captured_fetch == {
        "org_id": "org-123",
        "auth_token": _jwt({"organization": "org-123"}),
    }
    assert captured_merge == {
        "env": env_records,
        "store": store_records,
        "remote": remote_records,
    }
    assert result.resolved_integrations == {"datadog": {}, "grafana": {}, "sentry": {}}
    assert result.progress_message == (
        "Resolved integrations from remote, store, env: ['datadog', 'grafana', 'sentry']"
    )


def test_resolve_without_sources_reports_empty_local_lookup(monkeypatch: Any) -> None:
    monkeypatch.delenv("JWT_TOKEN", raising=False)
    monkeypatch.setattr("integrations.store.load_integrations", list)
    monkeypatch.setattr(resolution, "_load_env_integrations", list)

    result = resolution.resolve_integrations_with_metadata()

    assert result.resolved_integrations == {}
    assert result.progress_message is not None
    assert result.progress_message.startswith("No auth context and no local integrations found")
