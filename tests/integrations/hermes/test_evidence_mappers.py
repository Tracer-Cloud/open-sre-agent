"""Evidence mapper tests for Hermes investigation tools."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.hermes.tools.hermes_session_evidence_tool import _evidence as mappers

_UNAVAILABLE = {"available": False, "error": "requires a Hermes backend"}
_CASES: list[tuple[str, dict[str, Any], str, dict[str, Any]]] = [
    (
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
        "get_hermes_approval_events",
        {
            "available": True,
            "events": [{"command": "kubectl delete ns prod", "approval_kind": "never_prompted"}],
        },
        "1 event(s)",
        {"available": True, "events": []},
    ),
    (
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
        "get_hermes_cron_state",
        {
            "available": True,
            "schedule_cron": "0 */2 * * *",
            "last_run": {"delivery_status": "never_started"},
        },
        "schedule 0 */2 * * *, last delivery never_started",
        {"available": True, "schedule_cron": "", "last_run": {}},
    ),
    (
        "get_hermes_filesystem_state",
        {
            "available": True,
            "files": [{"path": "/tmp/hermes/memory/state.json", "is_corrupted": True}],
            "backups_present": False,
            "vcs_present": False,
        },
        "1 file(s), backups absent, no VCS",
        {"available": True, "files": []},
    ),
    (
        "get_hermes_kv_cache_state",
        {
            "available": True,
            "cache_hits": 14,
            "cache_misses": 36,
            "last_invalidated_reason": "role format drift",
            "messages_with_cache_miss": [{"message_index": 40}, {"message_index": 41}],
        },
        (
            "14 cache hit(s), 36 cache miss(es), invalidated: role format drift, "
            "2 cache-miss message(s)"
        ),
        {"available": True, "last_invalidated_reason": "", "messages_with_cache_miss": []},
    ),
    (
        "get_hermes_logs",
        {
            "records": [{"level": "ERROR", "message": "gateway crash"}],
            "incidents": [{"rule": "error_severity", "title": "ERROR burst"}],
        },
        "1 record(s), 1 incident(s)",
        {"records": [], "incidents": []},
    ),
    (
        "get_hermes_memory_state",
        {
            "available": True,
            "backend": "mempalace",
            "backend_status": "unreachable",
            "fallback_active": True,
            "fallback_reason": "External memory backend connection failed",
        },
        "mempalace, unreachable, fallback active, External memory backend connection failed",
        {"available": True, "backend": "", "backend_status": "", "fallback_active": False},
    ),
    (
        "get_hermes_memory_state",
        {
            "available": True,
            "backend": "in_process",
            "backend_status": "degraded",
            "last_parse_error": {
                "error_class": "JSONDecodeError",
                "snippet": '{ "memory": [1,2,], }',
                "model_name": "llama.cpp",
            },
        },
        "in_process, degraded, parse error JSONDecodeError (llama.cpp)",
        {"available": True, "backend": "", "backend_status": ""},
    ),
    (
        "get_hermes_message_history",
        {
            "available": True,
            "messages": [{"role": "system"}, {"role": "tool"}, {"role": "tool_call"}],
        },
        "3 message(s)",
        {"available": True, "messages": []},
    ),
    (
        "get_hermes_orchestration_state",
        {
            "available": True,
            "declared_roles": [{"name": "planner"}, {"name": "worker"}, {"name": "reviewer"}],
            "declared_topology": "planner_worker_reviewer",
            "observed": {
                "actual_topology": "single_agent_loop",
                "actual_runs": [{"role": "default"}],
            },
        },
        (
            "3 declared role(s), topology planner_worker_reviewer, "
            "observed single_agent_loop, 1 observed run(s)"
        ),
        {"available": True, "declared_roles": [], "declared_topology": "", "observed": {}},
    ),
]


@pytest.mark.parametrize(("source", "output", "summary", "empty"), _CASES)
def test_mapper_records_summary(
    source: str, output: dict[str, Any], summary: str, empty: dict[str, Any]
) -> None:
    mapper = mappers.MAPPERS[source]
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
    mappers.MAPPERS["get_hermes_credential_state"](
        evidence,
        {"available": True, "mode": "proxy", "in_memory_credential_count": 0, "outbound_calls": []},
        {},
    )
    assert evidence["catalog_entries"][0]["summary"] == "proxy mode, 0 in-memory credential(s)"
