"""Evidence mapper tests for Hermes session-evidence tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from integrations.hermes.tools.hermes_session_evidence_tool._evidence import (
    map_get_hermes_adapter_catalog,
    map_get_hermes_approval_events,
    map_get_hermes_audit_trail,
    map_get_hermes_config,
    map_get_hermes_credential_state,
    map_get_hermes_cron_state,
)

_Mapper = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None]
_UNAVAILABLE = {"available": False, "error": "requires a Hermes backend"}
_CASES: list[tuple[_Mapper, str, dict[str, Any], str, dict[str, Any]]] = [
    (
        map_get_hermes_adapter_catalog,
        "get_hermes_adapter_catalog",
        {
            "available": True,
            "messaging_adapters": ["slack", "discord"],
            "llm_providers": ["openai"],
            "execution_backends": ["local", "docker"],
        },
        "2 messaging adapter(s), 1 LLM provider(s), 2 execution backend(s)",
        {
            "available": True,
            "messaging_adapters": [],
            "llm_providers": [],
            "execution_backends": [],
        },
    ),
    (
        map_get_hermes_approval_events,
        "get_hermes_approval_events",
        {
            "available": True,
            "events": [{"command": "kubectl delete ns prod", "approval_kind": "never_prompted"}],
        },
        "1 event(s)",
        {"available": True, "events": []},
    ),
    (
        map_get_hermes_audit_trail,
        "get_hermes_audit_trail",
        {
            "available": True,
            "events": [
                {"action": "update_prod_routing_policy", "signature_present": False},
                {"action": "rotate_prod_credentials", "signature_present": False},
            ],
        },
        "2 event(s)",
        {"available": True, "events": []},
    ),
    (
        map_get_hermes_config,
        "get_hermes_config",
        {
            "available": True,
            "provider": "codex",
            "model": "gpt-5.4-mini",
            "region": "us-east-1",
            "providers": [{"name": "codex"}],
        },
        "codex, gpt-5.4-mini, us-east-1, 1 provider(s)",
        {"available": True, "provider": "", "model": "", "region": "", "providers": []},
    ),
    (
        map_get_hermes_credential_state,
        "get_hermes_credential_state",
        {
            "available": True,
            "mode": "in_process",
            "in_memory_credential_count": 2,
            "outbound_calls": [{"url": "https://api.example/v1/deploy"}],
        },
        "in_process mode, 2 in-memory credential(s), 1 outbound call(s)",
        {"available": True, "mode": "", "outbound_calls": []},
    ),
    (
        map_get_hermes_cron_state,
        "get_hermes_cron_state",
        {
            "available": True,
            "schedule_cron": "0 */2 * * *",
            "last_run": {"delivery_status": "never_started"},
        },
        "schedule 0 */2 * * *, last delivery never_started",
        {"available": True, "schedule_cron": "", "last_run": {}},
    ),
]


@pytest.mark.parametrize(("mapper", "source", "output", "summary", "empty"), _CASES)
def test_mapper_records_summary(
    mapper: _Mapper, source: str, output: dict[str, Any], summary: str, empty: dict[str, Any]
) -> None:
    evidence: dict[str, Any] = {}
    mapper(evidence, output, {})
    assert evidence["catalog_entries"][0]["source"] == source
    assert evidence["catalog_entries"][0]["summary"] == summary

    skipped: dict[str, Any] = {}
    mapper(skipped, empty, {})
    mapper(skipped, _UNAVAILABLE, {})
    assert "catalog_entries" not in skipped


def test_credential_state_records_zero_in_memory_count() -> None:
    evidence: dict[str, Any] = {}
    map_get_hermes_credential_state(
        evidence,
        {"available": True, "mode": "proxy", "in_memory_credential_count": 0, "outbound_calls": []},
        {},
    )
    assert evidence["catalog_entries"][0]["summary"] == "proxy mode, 0 in-memory credential(s)"
