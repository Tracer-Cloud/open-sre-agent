from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from tests.benchmarks.realrca_graph.features import clip_text
from tests.benchmarks.realrca_graph.models import EvidenceItem

_SPAN_ID_RE = re.compile(r"^span:([^:]+):(.+)$")
_RAW_FIELD_RE = re.compile(
    r'"(?P<key>server_ip|host_ip|client_ip|server_name|host_name)"\s*:\s*"(?P<value>[^"]+)"'
)
_ERROR_CODES = {"03", "3", "4", "timeout", "error", "biz_error"}


@dataclass(frozen=True)
class TraceHop:
    """One trace span normalized into an ontology call hop."""

    span_id: str
    trace_id: str
    rpc_id: str
    client: str
    server: str
    service: str
    duration_ms: float
    result_code: str
    server_ip: str = ""
    host_ip: str = ""

    @property
    def depth(self) -> int:
        return self.rpc_id.count(".")

    @property
    def is_abnormal(self) -> bool:
        return self.duration_ms >= 1000 or self.result_code.lower() in _ERROR_CODES

    def compact(self) -> str:
        client = self.client or "unknown-client"
        server = self.server or "unknown-server"
        service = self.service or "unknown-service"
        duration = (
            int(self.duration_ms) if self.duration_ms.is_integer() else round(self.duration_ms, 1)
        )
        code = f" rc={self.result_code}" if self.result_code else ""
        server_ip = f" server_ip={self.server_ip}" if self.server_ip else ""
        host_ip = (
            f" host_ip={self.host_ip}" if self.host_ip and self.host_ip != self.server_ip else ""
        )
        return f"{client} -> {server} {service} {duration}ms{code}{server_ip}{host_ip}"


@dataclass(frozen=True)
class TracePath:
    """A slow or failed trace hop with its available ancestor path."""

    trace_id: str
    hops: tuple[TraceHop, ...]

    @property
    def terminal(self) -> TraceHop:
        return self.hops[-1]

    def summary(self) -> str:
        chain = " | ".join(hop.compact() for hop in self.hops)
        return f"trace {self.trace_id} topology path: {chain}"


def topology_evidence_items(
    graph_context: dict[str, Any],
    *,
    start_index: int = 1,
    limit: int = 8,
) -> list[EvidenceItem]:
    """Build compact topology evidence rows from trace span graph relations."""

    items: list[EvidenceItem] = []
    for offset, path in enumerate(
        trace_paths_from_context(graph_context, limit=limit), start=start_index
    ):
        terminal = path.terminal
        items.append(
            EvidenceItem(
                id=f"e{offset}",
                name="topology_trace_path",
                modality="topology",
                summary=clip_text(path.summary(), 650),
                command="graph topology trace path",
                raw_ref=terminal.trace_id,
                score=round(1.55 + min(terminal.duration_ms / 20000.0, 0.6), 3),
            )
        )
    return items


def topology_root_candidates(
    graph_context: dict[str, Any],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Convert high-latency topology paths into root-cause candidates."""

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in trace_paths_from_context(graph_context, limit=limit):
        terminal = path.terminal
        label = terminal.service or terminal.server or terminal.span_id
        if _is_transport_or_entry_span(label):
            continue
        key = (terminal.trace_id, terminal.rpc_id, label)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "kind": "topology_trace_path",
                "label": label,
                "score": round(4.25 + min(terminal.duration_ms / 20000.0, 0.6), 3),
                "reason": f"slow/error call path in ontology graph: {clip_text(path.summary(), 360)}",
                "props": {
                    "trace_id": terminal.trace_id,
                    "rpc_id": terminal.rpc_id,
                    "client": terminal.client,
                    "server": terminal.server,
                    "service": terminal.service,
                    "duration_ms": terminal.duration_ms,
                    "result_code": terminal.result_code,
                    "server_ip": terminal.server_ip,
                    "host_ip": terminal.host_ip,
                    "top_signals": [
                        {
                            "kind": "trace_span",
                            "label": label,
                            "score": 4.9,
                            "reason": path.summary(),
                        }
                    ],
                },
            }
        )
    return candidates


def trace_paths_from_context(graph_context: dict[str, Any], *, limit: int = 8) -> list[TracePath]:
    """Return the most relevant slow/error trace paths from one graph context."""

    hops = _trace_hops(graph_context)
    by_trace: dict[str, list[TraceHop]] = defaultdict(list)
    for hop in hops:
        if hop.trace_id and hop.rpc_id:
            by_trace[hop.trace_id].append(hop)

    paths: list[TracePath] = []
    for trace_id, trace_hops in by_trace.items():
        by_rpc = {hop.rpc_id: hop for hop in trace_hops}
        for hop in trace_hops:
            if not hop.is_abnormal:
                continue
            chain = tuple(_ancestor_chain(hop, by_rpc))
            paths.append(TracePath(trace_id=trace_id, hops=chain))

    paths.sort(
        key=lambda path: (
            -path.terminal.duration_ms,
            path.terminal.result_code.lower() not in _ERROR_CODES,
            -len(path.hops),
            path.trace_id,
            path.terminal.rpc_id,
        )
    )
    return _dedupe_paths(paths)[:limit]


def _trace_hops(graph_context: dict[str, Any]) -> list[TraceHop]:
    nodes = {
        str(raw.get("id")): raw
        for raw in graph_context.get("nodes") or []
        if isinstance(raw, dict) and raw.get("id")
    }
    span_ids = [
        node_id
        for node_id, raw in nodes.items()
        if str(raw.get("kind") or "").lower() == "span" and _SPAN_ID_RE.match(node_id)
    ]
    labels = {node_id: str(raw.get("label") or node_id) for node_id, raw in nodes.items()}
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in graph_context.get("edges") or []:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or "")
        if source:
            outgoing[source].append(raw)

    hops: list[TraceHop] = []
    for span_id in span_ids:
        match = _SPAN_ID_RE.match(span_id)
        if match is None:
            continue
        raw = nodes[span_id]
        props = raw.get("props") if isinstance(raw.get("props"), dict) else {}
        raw_props = _raw_span_props(props.get("raw"))
        hops.append(
            TraceHop(
                span_id=span_id,
                trace_id=match.group(1),
                rpc_id=str(_first_field(props, raw_props, "rpc_id", "rpcId") or match.group(2)),
                client=_edge_label(outgoing, labels, span_id, "CLIENT")
                or str(_first_field(props, raw_props, "client", "clientName", "client_name") or ""),
                server=_edge_label(outgoing, labels, span_id, "SERVER")
                or str(
                    _first_field(
                        props, raw_props, "server", "serverName", "server_name", "host_name"
                    )
                    or ""
                ),
                service=_edge_label(outgoing, labels, span_id, "INVOKES")
                or str(
                    _first_field(
                        props,
                        raw_props,
                        "service",
                        "serviceName",
                        "service_dim_key",
                        "serviceDimKey",
                    )
                    or ""
                ),
                duration_ms=_float(
                    _first_field(props, raw_props, "duration_ms", "duration", "span") or 0
                ),
                result_code=str(
                    _first_field(
                        props, raw_props, "result_code", "resultCode", "resultType", "result_type"
                    )
                    or ""
                ).strip(),
                server_ip=str(_first_field(props, raw_props, "server_ip", "serverIp") or ""),
                host_ip=str(_first_field(props, raw_props, "host_ip", "hostIp") or ""),
            )
        )
    return hops


def _edge_label(
    outgoing: dict[str, list[dict[str, Any]]],
    labels: dict[str, str],
    source: str,
    rel: str,
) -> str:
    for edge in outgoing.get(source, []):
        if edge.get("rel") == rel:
            return labels.get(str(edge.get("target") or ""), "")
    return ""


def _ancestor_chain(hop: TraceHop, by_rpc: dict[str, TraceHop]) -> list[TraceHop]:
    chain = [hop]
    rpc_id = hop.rpc_id
    while "." in rpc_id:
        rpc_id = rpc_id.rsplit(".", 1)[0]
        parent = by_rpc.get(rpc_id)
        if parent is not None:
            chain.append(parent)
    return sorted(chain, key=lambda item: (item.depth, item.rpc_id))


def _dedupe_paths(paths: list[TracePath]) -> list[TracePath]:
    output: list[TracePath] = []
    seen: set[tuple[str, str, str]] = set()
    for path in paths:
        terminal = path.terminal
        key = (path.trace_id, terminal.server, terminal.service)
        if key in seen:
            continue
        seen.add(key)
        output.append(path)
    return output


def _is_transport_or_entry_span(label: str) -> bool:
    lower = label.strip().lower()
    return lower.startswith(
        (
            "http://",
            "https://",
            "http@",
            "rest:",
            "rest:/",
            "mqrecv@",
            "schedulerxjobexec",
        )
    )


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_field(props: dict[str, Any], raw_props: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = props.get(name)
        if value not in (None, ""):
            return value
        value = raw_props.get(name)
        if value not in (None, ""):
            return value
    return None


def _raw_span_props(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    return {match.group("key"): match.group("value") for match in _RAW_FIELD_RE.finditer(value)}
