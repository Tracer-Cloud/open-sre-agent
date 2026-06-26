"""Structured Hermes session evidence tools for incident investigation."""

from __future__ import annotations

from typing import Any, cast

from tools.tool_decorator import tool


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    hermes = sources.get("hermes", {})
    return {
        "session_id": str(hermes.get("session_id") or ""),
        "hermes_backend": hermes.get("_backend"),
    }


def _backend_or_error(hermes_backend: Any, tool_name: str) -> Any:
    if hermes_backend is None:
        return {
            "source": "hermes",
            "available": False,
            "error": (
                f"{tool_name} requires a Hermes backend. Configure Hermes integration "
                "or inject a fixture backend for synthetic/e2e runs."
            ),
        }
    return hermes_backend


def _fixture_backend_only(sources: dict[str, Any]) -> bool:
    hermes = sources.get("hermes")
    return isinstance(hermes, dict) and hermes.get("_backend") is not None


@tool(
    name="get_hermes_session_log",
    source="hermes",
    description="Get structured Hermes session event log entries.",
    use_cases=["Inspect message/tool/error/retry event sequence for a Hermes session"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_session_log(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_session_log")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_session_log(session_id=session_id))


@tool(
    name="get_hermes_provider_traffic",
    source="hermes",
    description="Get captured Hermes provider HTTP/SSE request and response traffic.",
    use_cases=[
        "Diagnose provider 4xx/5xx responses, malformed bodies, dropped headers, and SSE drift"
    ],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_provider_traffic(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_provider_traffic")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_provider_traffic(session_id=session_id))


@tool(
    name="get_hermes_adapter_catalog",
    source="hermes",
    description="Get Hermes adapter catalog and registered surface families.",
    use_cases=[
        "Identify messaging adapters, LLM providers, execution backends, and unknown adapter attribution"
    ],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_adapter_catalog(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_adapter_catalog")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_adapter_catalog(session_id=session_id))


@tool(
    name="get_hermes_config",
    source="hermes",
    description="Get resolved Hermes provider, model, region, and transport configuration.",
    use_cases=[
        "Diagnose provider selection, Bedrock IMDS overrides, transport limits, and adapter config mismatches"
    ],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_config(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_config")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_config(session_id=session_id))


@tool(
    name="get_hermes_message_history",
    source="hermes",
    description="Get full Hermes conversation message history for ordering/invariant checks.",
    use_cases=["Detect malformed tool_call/tool sequencing after compression"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_message_history(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_message_history")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_message_history(session_id=session_id))


@tool(
    name="get_hermes_kv_cache_state",
    source="hermes",
    description="Get Hermes KV cache counters and miss-diff diagnostics.",
    use_cases=["Diagnose cache-thrash caused by formatting drift"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_kv_cache_state(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_kv_cache_state")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_kv_cache_state(session_id=session_id))


@tool(
    name="get_hermes_runtime_state",
    source="hermes",
    description="Get Hermes runtime state including queue depth/progress timestamps.",
    use_cases=["Diagnose hangs via deterministic frozen_now_ts vs last_progress_ts"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_runtime_state(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_runtime_state")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_runtime_state(session_id=session_id))


@tool(
    name="get_hermes_cron_state",
    source="hermes",
    description="Get Hermes cron execution and delivery timing state.",
    use_cases=["Differentiate agent completion from downstream delivery hangs"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_cron_state(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_cron_state")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_cron_state(session_id=session_id))


@tool(
    name="get_hermes_session_topology",
    source="hermes",
    description="Get Hermes visible/continuation session topology for ghost-session detection.",
    use_cases=["Follow continuation_of chains to detect invisible forked sessions"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_session_topology(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_session_topology")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_session_topology(session_id=session_id))


@tool(
    name="get_hermes_orchestration_state",
    source="hermes",
    description="Get Hermes orchestration role/topology execution state.",
    use_cases=["Diagnose collapsed orchestration, isolated ACP sessions, and role execution drift"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_orchestration_state(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_orchestration_state")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_orchestration_state(session_id=session_id))


@tool(
    name="get_hermes_routing_decisions",
    source="hermes",
    description="Get Hermes capability routing decisions and model selection outcomes.",
    use_cases=["Diagnose ignored routing policies and default-model fallback behavior"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_routing_decisions(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_routing_decisions")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_routing_decisions(session_id=session_id))


@tool(
    name="get_hermes_memory_state",
    source="hermes",
    description="Get Hermes memory backend health and parse/fallback state.",
    use_cases=["Diagnose memory backend outages, corruption, and parse failures"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_memory_state(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_memory_state")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_memory_state(session_id=session_id))


@tool(
    name="get_hermes_filesystem_state",
    source="hermes",
    description="Get Hermes filesystem persistence and corruption state.",
    use_cases=["Diagnose corrupted memory snapshots and missing recovery backups"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_filesystem_state(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_filesystem_state")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_filesystem_state(session_id=session_id))


@tool(
    name="get_hermes_audit_trail",
    source="hermes",
    description="Get Hermes audit policy and observed audit-chain/signature state.",
    use_cases=["Diagnose missing cryptographic audit trails and broken hash chains"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_audit_trail(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_audit_trail")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_audit_trail(session_id=session_id))


@tool(
    name="get_hermes_approval_events",
    source="hermes",
    description="Get Hermes approval prompts and destructive-command approval outcomes.",
    use_cases=["Diagnose destructive commands that ran without explicit approval"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_approval_events(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_approval_events")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_approval_events(session_id=session_id))


@tool(
    name="get_hermes_rbac_state",
    source="hermes",
    description="Get Hermes tenant scopes and observed cross-tenant access checks.",
    use_cases=["Diagnose missing RBAC checks and cross-tenant memory/context access"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_rbac_state(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_rbac_state")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_rbac_state(session_id=session_id))


@tool(
    name="get_hermes_credential_state",
    source="hermes",
    description="Get Hermes credential isolation mode and outbound credential usage.",
    use_cases=["Diagnose in-process credential exposure and missing credential proxy isolation"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_credential_state(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_credential_state")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_credential_state(session_id=session_id))


@tool(
    name="get_hermes_workflow_run",
    source="hermes",
    description="Get Hermes deterministic workflow run comparison state.",
    use_cases=["Diagnose non-deterministic workflow output drift across same-input runs"],
    surfaces=("investigation",),
    input_schema={
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    },
    is_available=_fixture_backend_only,
    extract_params=_extract_params,
)
def get_hermes_workflow_run(
    session_id: str = "",
    hermes_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    backend = _backend_or_error(hermes_backend, "get_hermes_workflow_run")
    if isinstance(backend, dict):
        return backend
    return cast(dict[str, Any], backend.get_workflow_run(session_id=session_id))


__all__ = [
    "get_hermes_session_log",
    "get_hermes_provider_traffic",
    "get_hermes_adapter_catalog",
    "get_hermes_config",
    "get_hermes_message_history",
    "get_hermes_kv_cache_state",
    "get_hermes_runtime_state",
    "get_hermes_cron_state",
    "get_hermes_session_topology",
    "get_hermes_orchestration_state",
    "get_hermes_routing_decisions",
    "get_hermes_memory_state",
    "get_hermes_filesystem_state",
    "get_hermes_audit_trail",
    "get_hermes_approval_events",
    "get_hermes_rbac_state",
    "get_hermes_credential_state",
    "get_hermes_workflow_run",
]
