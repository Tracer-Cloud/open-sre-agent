"""Evidence mappers for Hermes session-evidence investigation tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.domain.types.evidence import EvidenceMapper, record_evidence_entry

_Summarize = Callable[[dict[str, Any]], str | None]


def _mapper(source: str, label: str, summarize: _Summarize) -> EvidenceMapper:
    def map_output(
        evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
    ) -> None:
        if not output.get("available"):
            return
        summary = summarize(output)
        if summary:
            record_evidence_entry(evidence, source=source, label=label, summary=summary)

    map_output.__name__ = f"map_{source}"
    return map_output


def _count(output: dict[str, Any], key: str) -> int:
    rows = output.get(key) or []
    return len(rows) if isinstance(rows, list) else 0


def _catalog(output: dict[str, Any]) -> str | None:
    counts = (
        _count(output, "messaging_adapters"),
        _count(output, "llm_providers"),
        _count(output, "execution_backends"),
    )
    if not any(counts):
        return None
    return (
        f"{counts[0]} messaging adapter(s), "
        f"{counts[1]} LLM provider(s), "
        f"{counts[2]} execution backend(s)"
    )


def _config(output: dict[str, Any]) -> str | None:
    parts = [str(output.get(key) or "") for key in ("provider", "model", "region")]
    n_providers = _count(output, "providers")
    if n_providers:
        parts.append(f"{n_providers} provider(s)")
    return ", ".join(part for part in parts if part) or None


def _cron(output: dict[str, Any]) -> str | None:
    last_run = output.get("last_run")
    status = last_run.get("delivery_status") if isinstance(last_run, dict) else None
    parts = []
    if schedule := str(output.get("schedule_cron") or ""):
        parts.append(f"schedule {schedule}")
    if status:
        parts.append(f"last delivery {status}")
    return ", ".join(parts) or None


def _events(output: dict[str, Any]) -> str | None:
    n_events = _count(output, "events")
    return f"{n_events} event(s)" if n_events else None


def _credentials(output: dict[str, Any]) -> str | None:
    parts = []
    if mode := str(output.get("mode") or ""):
        parts.append(f"{mode} mode")
    in_memory = output.get("in_memory_credential_count")
    if isinstance(in_memory, int):
        parts.append(f"{in_memory} in-memory credential(s)")
    if n_outbound := _count(output, "outbound_calls"):
        parts.append(f"{n_outbound} outbound call(s)")
    return ", ".join(parts) or None


map_get_hermes_adapter_catalog = _mapper(
    "get_hermes_adapter_catalog", "Hermes Adapter Catalog", _catalog
)
map_get_hermes_config = _mapper("get_hermes_config", "Hermes Config", _config)
map_get_hermes_cron_state = _mapper("get_hermes_cron_state", "Hermes Cron State", _cron)
map_get_hermes_audit_trail = _mapper("get_hermes_audit_trail", "Hermes Audit Trail", _events)
map_get_hermes_approval_events = _mapper(
    "get_hermes_approval_events", "Hermes Approval Events", _events
)
map_get_hermes_credential_state = _mapper(
    "get_hermes_credential_state", "Hermes Credential State", _credentials
)
