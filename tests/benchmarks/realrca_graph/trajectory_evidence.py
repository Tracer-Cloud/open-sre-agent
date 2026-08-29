from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from tests.benchmarks.realrca_graph.access_logs import access_log_signals
from tests.benchmarks.realrca_graph.app_logs import app_log_signals
from tests.benchmarks.realrca_graph.rds_sql import rds_sql_signals
from tests.benchmarks.realrca_graph.sql_logs import sql_log_signals


def augment_graph_context_with_trajectory(
    graph_context: dict[str, Any],
    run_payload: dict[str, Any],
    *,
    source: str = "trajectory",
) -> dict[str, Any]:
    """Append visible tool-result observations from a DMA trajectory to a graph context."""

    augmented = deepcopy(graph_context)
    evidence = augmented.setdefault("evidence", [])
    root_candidates = augmented.setdefault("root_candidates", [])
    tool_commands = _tool_commands_by_id(run_payload)
    seen_candidates = {
        (str(item.get("kind") or ""), str(item.get("label") or ""))
        for item in root_candidates
        if isinstance(item, dict)
    }
    added_indices: dict[tuple[str, str], tuple[int, int]] = {}
    sequence = 0
    for result in _tool_results(run_payload):
        content = str(result.get("content") or "")
        parsed = _parse_json_content(content)
        if parsed is None:
            continue
        signals = _trajectory_signals(parsed)
        if not signals:
            continue
        tool_use_id = str(result.get("tool_use_id") or "")
        command = tool_commands.get(tool_use_id, "")
        for signal in signals:
            key = (signal["kind"], signal["label"])
            if key in seen_candidates:
                indices = added_indices.get(key)
                if indices is not None:
                    candidate_index, evidence_index = indices
                    current_candidate = root_candidates[candidate_index]
                    if _signal_quality(signal) > _candidate_quality(current_candidate):
                        evidence[evidence_index] = _evidence_payload(
                            source, sequence, signal, command
                        )
                        root_candidates[candidate_index] = _candidate_payload(
                            signal, source, tool_use_id
                        )
                continue
            seen_candidates.add(key)
            sequence += 1
            evidence.append(_evidence_payload(source, sequence, signal, command))
            root_candidates.append(_candidate_payload(signal, source, tool_use_id))
            added_indices[key] = (len(root_candidates) - 1, len(evidence) - 1)
    return augmented


def _tool_commands_by_id(run_payload: dict[str, Any]) -> dict[str, str]:
    commands: dict[str, str] = {}
    for event in run_payload.get("output") or []:
        if not isinstance(event, dict) or event.get("event") != "agent.tool_use":
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        for argument in data.get("arguments") or []:
            if not isinstance(argument, dict):
                continue
            tool_use_id = str(argument.get("id") or "")
            tool_input = argument.get("input") if isinstance(argument.get("input"), dict) else {}
            command = str(tool_input.get("command") or "").strip()
            if tool_use_id and command:
                commands[tool_use_id] = command
    return commands


def _tool_results(run_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in run_payload.get("output") or []:
        if not isinstance(event, dict) or event.get("event") != "agent.tool_result":
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        for result in data.get("result") or []:
            if isinstance(result, dict):
                rows.append(result)
    return rows


def _parse_json_content(content: str) -> Any:
    stripped = content.strip()
    if not stripped:
        return None
    if stripped[0] in "[{":
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    rows: list[Any] = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line or line[0] not in "[{":
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            rows.extend(parsed)
        else:
            rows.append(parsed)
    return rows or None


def _trajectory_signals(parsed: Any) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for signal in sql_log_signals(parsed):
        signals.append(
            _signal_payload(
                kind="sql_log_error",
                label=signal.label,
                score=signal.score,
                reason=signal.reason,
                summary=signal.summary,
                trace_ids=signal.trace_ids,
                props=signal.props,
                evidence_family="sls_sql",
            )
        )
    for signal in access_log_signals(parsed):
        signals.append(
            _signal_payload(
                kind="http_access_error",
                label=signal.label,
                score=signal.score,
                reason=signal.reason,
                summary=signal.summary,
                trace_ids=signal.trace_ids,
                props=signal.props,
                evidence_family="sls_access",
            )
        )
    for signal in rds_sql_signals(parsed):
        signal_family = str(signal.props.get("signal_family") or "")
        kind = "rds_sql_detail" if signal_family == "rds_sql_detail" else "rds_sql_stat"
        signals.append(
            _signal_payload(
                kind=kind,
                label=signal.label,
                score=signal.score,
                reason=signal.reason,
                summary=signal.summary,
                trace_ids=[],
                props=signal.props,
                evidence_family="rds_sql",
            )
        )
    for signal in app_log_signals(parsed):
        signals.append(
            _signal_payload(
                kind=signal.kind,
                label=signal.label,
                score=signal.score,
                reason=signal.reason,
                summary=signal.summary,
                trace_ids=signal.trace_ids,
                props=signal.props,
                evidence_family="sls_app",
            )
        )
    return signals


def _signal_payload(
    *,
    kind: str,
    label: str,
    score: float,
    reason: str,
    summary: str,
    trace_ids: list[str],
    props: dict[str, Any],
    evidence_family: str,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "score": score,
        "reason": reason,
        "summary": summary,
        "trace_ids": trace_ids,
        "props": props,
        "evidence_family": evidence_family,
    }


def _evidence_payload(
    source: str, sequence: int, signal: dict[str, Any], command: str
) -> dict[str, Any]:
    return {
        "name": f"{source}_{signal['evidence_family']}_{signal['kind']}_{sequence}",
        "command": command,
        "returncode": 0,
        "summary": f"trajectory_{signal['evidence_family']}_signal {signal['summary']}",
        "raw_path": "",
        "parse_error": "",
    }


def _candidate_payload(signal: dict[str, Any], source: str, tool_use_id: str) -> dict[str, Any]:
    return {
        "kind": signal["kind"],
        "label": signal["label"],
        "score": signal["score"],
        "reason": signal["reason"],
        "props": {
            **signal["props"],
            "trace_ids": signal["trace_ids"],
            "source": source,
            "tool_use_id": tool_use_id,
        },
    }


def _candidate_quality(candidate: dict[str, Any]) -> tuple[int, int, int, int, int, float]:
    props = candidate.get("props") if isinstance(candidate.get("props"), dict) else {}
    return (
        len(props.get("trace_ids") or []),
        len(props.get("stale_packet_ms") or []),
        int(bool(props.get("sql_table"))),
        int(bool(props.get("sql_id"))),
        int(props.get("count") or 0),
        float(candidate.get("score") or 0.0),
    )


def _signal_quality(signal: dict[str, Any]) -> tuple[int, int, int, int, int, float]:
    props = signal["props"] if isinstance(signal.get("props"), dict) else {}
    return (
        len(signal.get("trace_ids") or []),
        len(props.get("stale_packet_ms") or []),
        int(bool(props.get("sql_table"))),
        int(bool(props.get("sql_id"))),
        int(props.get("count") or 0),
        float(signal.get("score") or 0.0),
    )
