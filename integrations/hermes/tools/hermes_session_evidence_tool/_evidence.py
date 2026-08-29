"""Evidence mappers for Hermes investigation tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.domain.types.evidence import EvidenceMapper, record_evidence_entry

_Summarize = Callable[[dict[str, Any]], str | None]


def _mapper(source: str, label: str, summarize: _Summarize) -> EvidenceMapper:
    def map_output(
        evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
    ) -> None:
        if output.get("available") is not False and (summary := summarize(output)):
            record_evidence_entry(evidence, source=source, label=label, summary=summary)

    map_output.__name__ = f"map_{source}"
    return map_output


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _count(output: dict[str, Any], key: str) -> int:
    rows = output.get(key) or []
    return len(rows) if isinstance(rows, list) else 0


def _join(*parts: str | None) -> str | None:
    return ", ".join(part for part in parts if part) or None


def _str(data: dict[str, Any], key: str, prefix: str = "") -> str | None:
    value = str(data.get(key) or "")
    return f"{prefix}{value}" if value else None


def _int(data: dict[str, Any], key: str, noun: str) -> str | None:
    value = data.get(key)
    return f"{value} {noun}" if isinstance(value, int) else None


def _n(data: dict[str, Any], key: str, noun: str) -> str | None:
    n = _count(data, key)
    return f"{n} {noun}" if n else None


def _flag(data: dict[str, Any], key: str, yes: str, no: str) -> str | None:
    value = data.get(key)
    return None if value is None else (yes if value else no)


def _counts(*fields: tuple[str, str]) -> _Summarize:
    return lambda output: _join(*(_n(output, key, noun) for key, noun in fields))


def _catalog(output: dict[str, Any]) -> str | None:
    a, b, c = (
        _count(output, k) for k in ("messaging_adapters", "llm_providers", "execution_backends")
    )
    if not (a or b or c):
        return None
    return f"{a} messaging adapter(s), {b} LLM provider(s), {c} execution backend(s)"


def _config(output: dict[str, Any]) -> str | None:
    return _join(
        *(_str(output, key) for key in ("provider", "model", "region")),
        _n(output, "providers", "provider(s)"),
    )


def _cron(output: dict[str, Any]) -> str | None:
    return _join(
        _str(output, "schedule_cron", "schedule "),
        _str(_as_dict(output.get("last_run")), "delivery_status", "last delivery "),
    )


def _credentials(output: dict[str, Any]) -> str | None:
    mode = _str(output, "mode")
    return _join(
        f"{mode} mode" if mode else None,
        _int(output, "in_memory_credential_count", "in-memory credential(s)"),
        _n(output, "outbound_calls", "outbound call(s)"),
    )


def _filesystem(output: dict[str, Any]) -> str | None:
    return _join(
        _n(output, "files", "file(s)"),
        _flag(output, "backups_present", "backups present", "backups absent"),
        _flag(output, "vcs_present", "VCS present", "no VCS"),
    )


def _kv_cache(output: dict[str, Any]) -> str | None:
    return _join(
        _int(output, "cache_hits", "cache hit(s)"),
        _int(output, "cache_misses", "cache miss(es)"),
        _str(output, "last_invalidated_reason", "invalidated: "),
        _n(output, "messages_with_cache_miss", "cache-miss message(s)"),
    )


def _memory(output: dict[str, Any]) -> str | None:
    fallback = output.get("fallback_active")
    err = _as_dict(output.get("last_parse_error"))
    error_class = _str(err, "error_class")
    model = _str(err, "model_name")
    parse = f"parse error {error_class}" + (f" ({model})" if model else "") if error_class else None
    return _join(
        _str(output, "backend"),
        _str(output, "backend_status"),
        "fallback active" if fallback else None,
        _str(output, "fallback_reason") if fallback else None,
        parse,
    )


def _orchestration(output: dict[str, Any]) -> str | None:
    observed = _as_dict(output.get("observed"))
    return _join(
        _n(output, "declared_roles", "declared role(s)"),
        _str(output, "declared_topology", "topology "),
        _str(observed, "actual_topology", "observed "),
        _n(observed, "actual_runs", "observed run(s)"),
    )


_events = _counts(("events", "event(s)"))
MAPPERS: dict[str, EvidenceMapper] = {
    source: _mapper(source, label, summarize)
    for source, label, summarize in (
        ("get_hermes_adapter_catalog", "Hermes Adapter Catalog", _catalog),
        ("get_hermes_config", "Hermes Config", _config),
        ("get_hermes_cron_state", "Hermes Cron State", _cron),
        ("get_hermes_audit_trail", "Hermes Audit Trail", _events),
        ("get_hermes_approval_events", "Hermes Approval Events", _events),
        ("get_hermes_credential_state", "Hermes Credential State", _credentials),
        ("get_hermes_filesystem_state", "Hermes Filesystem State", _filesystem),
        ("get_hermes_kv_cache_state", "Hermes KV Cache State", _kv_cache),
        (
            "get_hermes_logs",
            "Hermes Logs",
            _counts(("records", "record(s)"), ("incidents", "incident(s)")),
        ),
        ("get_hermes_memory_state", "Hermes Memory State", _memory),
        (
            "get_hermes_message_history",
            "Hermes Message History",
            _counts(("messages", "message(s)")),
        ),
        ("get_hermes_orchestration_state", "Hermes Orchestration State", _orchestration),
    )
}
