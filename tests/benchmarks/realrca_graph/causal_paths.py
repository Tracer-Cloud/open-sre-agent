from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, dataclass
from typing import Any

from tests.benchmarks.realrca_graph.models import EvidenceBundle, RootHypothesis
from tests.benchmarks.realrca_graph.ontology_graph import GraphEdge, GraphNode, OntologyGraph

ROOT_SEED_KINDS = {
    "app",
    "change",
    "endpoint",
    "ip",
    "metric_series",
    "service",
    "span",
    "trace",
}
SYMPTOM_KINDS = {"alarm", "metric_series"}
HIGH_FANOUT_RELATIONS = {"CHANGE_TOUCHES", "SPAN_MENTIONS"}
DIRECT_EVIDENCE_RELATIONS = {
    "CALLS",
    "CLIENT",
    "ENDPOINT_OF",
    "HAS_SPAN",
    "INVOKES",
    "MENTIONS",
    "RAISED_ON",
    "SERVER",
}
RELATION_PRIORITY = {
    "HAS_SPAN": 0,
    "INVOKES": 1,
    "CALLS": 2,
    "SERVER": 3,
    "CLIENT": 4,
    "ENDPOINT_OF": 5,
    "RAISED_ON": 6,
    "HAS_ALARM": 7,
    "MENTIONS": 8,
    "HAS_TAG": 9,
    "HAS_CHANGE": 10,
    "CHANGE_TOUCHES": 20,
    "SPAN_MENTIONS": 21,
}


@dataclass(frozen=True)
class CausalPathEdge:
    """One edge in a compact root-to-symptom evidence path."""

    source: str
    rel: str
    target: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class CausalPathNode:
    """One node in a compact evidence path."""

    id: str
    kind: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class HypothesisCausalPath:
    """Path evidence for one root hypothesis."""

    hypothesis_id: str
    hypothesis_kind: str
    hypothesis_label: str
    root_layer: str
    hypothesis_score: float
    path_score: float
    path_length: int | None
    seed_nodes: list[CausalPathNode]
    path_nodes: list[CausalPathNode]
    path_edges: list[CausalPathEdge]
    risk_flags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "hypothesis_kind": self.hypothesis_kind,
            "hypothesis_label": self.hypothesis_label,
            "root_layer": self.root_layer,
            "hypothesis_score": self.hypothesis_score,
            "path_score": self.path_score,
            "path_length": self.path_length,
            "seed_nodes": [item.to_dict() for item in self.seed_nodes],
            "path_nodes": [item.to_dict() for item in self.path_nodes],
            "path_edges": [item.to_dict() for item in self.path_edges],
            "risk_flags": list(self.risk_flags),
        }


@dataclass(frozen=True)
class CausalPathReport:
    """Causal path audit for the hypotheses in one evidence bundle."""

    case_id: str
    case_type: str
    symptom_nodes: list[CausalPathNode]
    hypothesis_count: int
    risk_counts: dict[str, int]
    hypotheses: list[HypothesisCausalPath]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_type": self.case_type,
            "hidden_test_reference_used": False,
            "symptom_nodes": [item.to_dict() for item in self.symptom_nodes],
            "hypothesis_count": self.hypothesis_count,
            "risk_counts": dict(self.risk_counts),
            "hypotheses": [item.to_dict() for item in self.hypotheses],
        }


def build_causal_path_report(
    graph_context: dict[str, Any],
    bundle: EvidenceBundle,
    *,
    max_depth: int = 5,
    seed_limit: int = 8,
) -> CausalPathReport:
    """Score graph paths from each root hypothesis to the case symptom."""

    graph = OntologyGraph.from_context(graph_context)
    symptom_ids = _symptom_ids(graph)
    hypotheses = [
        _hypothesis_path(
            graph=graph,
            hypothesis=hypothesis,
            symptom_ids=symptom_ids,
            max_depth=max_depth,
            seed_limit=seed_limit,
        )
        for hypothesis in bundle.hypotheses
    ]
    hypotheses.sort(
        key=lambda item: (-item.path_score, item.path_length or 999, -item.hypothesis_score)
    )
    return CausalPathReport(
        case_id=bundle.case_id,
        case_type=bundle.case_type,
        symptom_nodes=[
            _compact_node(graph.nodes[node_id])
            for node_id in sorted(symptom_ids)
            if node_id in graph.nodes
        ],
        hypothesis_count=len(hypotheses),
        risk_counts=dict(Counter(flag for item in hypotheses for flag in item.risk_flags)),
        hypotheses=hypotheses,
    )


def render_causal_path_markdown(report: CausalPathReport, *, limit: int = 20) -> str:
    """Render a compact path audit for RCA review."""

    lines = [
        "# RealRCA Causal Path Audit",
        "",
        f"- case_id: `{report.case_id}`",
        f"- case_type: `{report.case_type}`",
        "- hidden_test_reference_used: `False`",
        f"- symptoms: `{', '.join(node.label for node in report.symptom_nodes[:5])}`",
        f"- risk_counts: `{_top_counts(report.risk_counts)}`",
        "",
        "| rank | hypothesis | kind | layer | path | score | risks |",
        "| --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for index, item in enumerate(report.hypotheses[:limit], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{item.hypothesis_label}`",
                    item.hypothesis_kind,
                    item.root_layer,
                    str(item.path_length) if item.path_length is not None else "-",
                    f"{item.path_score:.3f}",
                    ",".join(item.risk_flags) or "-",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Path Notes", ""])
    for item in report.hypotheses[:limit]:
        path = " -> ".join(f"{node.kind}:{node.label}" for node in item.path_nodes)
        rels = ", ".join(edge.rel for edge in item.path_edges)
        lines.extend(
            [
                f"### `{item.hypothesis_label}`",
                "",
                f"- score: `{item.path_score}` path_length: `{item.path_length}` risks: `{item.risk_flags}`",
                f"- seeds: `{', '.join(node.label for node in item.seed_nodes[:5])}`",
                f"- path: {path or '-'}",
                f"- relations: `{rels or '-'}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _hypothesis_path(
    *,
    graph: OntologyGraph,
    hypothesis: RootHypothesis,
    symptom_ids: set[str],
    max_depth: int,
    seed_limit: int,
) -> HypothesisCausalPath:
    seeds = _seed_nodes(graph, hypothesis, limit=seed_limit)
    path_node_ids, path_edges = _best_path(
        graph, [node.id for node in seeds], symptom_ids, max_depth=max_depth
    )
    flags = _risk_flags(graph, seeds, path_edges, path_node_ids)
    path_score = _path_score(path_edges, flags)
    return HypothesisCausalPath(
        hypothesis_id=hypothesis.id,
        hypothesis_kind=hypothesis.kind,
        hypothesis_label=hypothesis.label,
        root_layer=hypothesis.root_layer,
        hypothesis_score=hypothesis.score,
        path_score=path_score,
        path_length=len(path_edges) if path_edges else None,
        seed_nodes=[_compact_node(node) for node in seeds],
        path_nodes=[
            _compact_node(graph.nodes[node_id])
            for node_id in path_node_ids
            if node_id in graph.nodes
        ],
        path_edges=[CausalPathEdge(edge.source, edge.rel, edge.target) for edge in path_edges],
        risk_flags=flags,
    )


def _seed_nodes(graph: OntologyGraph, hypothesis: RootHypothesis, *, limit: int) -> list[GraphNode]:
    seed_text = {
        "label": hypothesis.label,
        "kind": hypothesis.kind,
        "reason": hypothesis.reason,
        "entities": hypothesis.entities,
    }
    hits = graph.node_hits_for_text(seed_text, kinds=ROOT_SEED_KINDS, limit=limit * 2)
    if not hits:
        return []
    ranked = sorted(
        hits, key=lambda item: (-item.overlap, _kind_priority(item.node.kind), item.node.label)
    )
    return [hit.node for hit in ranked[:limit]]


def _symptom_ids(graph: OntologyGraph) -> set[str]:
    symptom_ids = {node.id for node in graph.nodes.values() if node.kind in SYMPTOM_KINDS}
    for alarm_id in [node_id for node_id in symptom_ids if graph.nodes[node_id].kind == "alarm"]:
        for edge in graph.incident_edges(alarm_id):
            if edge.rel != "MENTIONS":
                continue
            target = edge.target if edge.source == alarm_id else edge.source
            node = graph.nodes.get(target)
            if node is not None and node.kind == "trace":
                symptom_ids.add(target)
    return {node_id for node_id in symptom_ids if node_id in graph.nodes}


def _best_path(
    graph: OntologyGraph,
    seed_ids: list[str],
    symptom_ids: set[str],
    *,
    max_depth: int,
) -> tuple[list[str], list[GraphEdge]]:
    queue = deque(
        (seed_id, seed_id, [seed_id], []) for seed_id in seed_ids if seed_id in graph.nodes
    )
    seen = {(seed_id, seed_id) for seed_id in seed_ids if seed_id in graph.nodes}
    best: tuple[list[str], list[GraphEdge]] = ([], [])
    best_score = -1.0
    while queue:
        start_id, node_id, path_nodes, path_edges = queue.popleft()
        if node_id in symptom_ids and path_edges:
            flags = _risk_flags(graph, [], path_edges, path_nodes)
            score = _path_score(path_edges, flags)
            if score > best_score:
                best = (path_nodes, path_edges)
                best_score = score
            continue
        if len(path_edges) >= max_depth:
            continue
        for edge in _ordered_incident_edges(graph, node_id):
            neighbor = edge.target if edge.source == node_id else edge.source
            if neighbor not in graph.nodes:
                continue
            if neighbor in path_nodes:
                continue
            seen_key = (start_id, neighbor)
            if seen_key in seen:
                continue
            seen.add(seen_key)
            queue.append((start_id, neighbor, path_nodes + [neighbor], path_edges + [edge]))
    return best


def _path_score(edges: list[GraphEdge], flags: list[str]) -> float:
    if not edges:
        return 0.0
    direct_relations = sum(1 for edge in edges if edge.rel in DIRECT_EVIDENCE_RELATIONS)
    base = 1.0 / len(edges)
    score = base + min(0.8, direct_relations * 0.18)
    if "high_fanout_bridge" in flags:
        score -= 0.35
    if "change_without_direct_symptom_path" in flags:
        score -= 0.25
    if "span_mentions_bridge" in flags:
        score -= 0.12
    return round(max(0.0, min(1.0, score)), 3)


def _risk_flags(
    graph: OntologyGraph,
    seeds: list[GraphNode],
    edges: list[GraphEdge],
    path_node_ids: list[str],
) -> list[str]:
    flags: list[str] = []
    if not edges:
        return ["no_symptom_path"]
    if any(
        edge.rel in HIGH_FANOUT_RELATIONS and _edge_fanout(graph, edge) >= 30 for edge in edges
    ) or any(_node_high_fanout(graph, seed.id) for seed in seeds):
        flags.append("high_fanout_bridge")
    if any(edge.rel == "SPAN_MENTIONS" for edge in edges):
        flags.append("span_mentions_bridge")
    path_kinds = {graph.nodes[node_id].kind for node_id in path_node_ids if node_id in graph.nodes}
    seed_kinds = {node.kind for node in seeds}
    if "change" in path_kinds | seed_kinds and not any(
        edge.rel == "HAS_CHANGE" for edge in edges[:2]
    ):
        flags.append("change_without_direct_symptom_path")
    return flags


def _edge_fanout(graph: OntologyGraph, edge: GraphEdge) -> int:
    return max(
        len(graph.incident_edges(edge.source, rel=edge.rel)),
        len(graph.incident_edges(edge.target, rel=edge.rel)),
    )


def _node_high_fanout(graph: OntologyGraph, node_id: str) -> bool:
    node = graph.nodes.get(node_id)
    if node is None or node.kind not in {"app", "change"}:
        return False
    relation_counts = Counter(edge.rel for edge in graph.incident_edges(node_id))
    return any(relation_counts[rel] >= 30 for rel in HIGH_FANOUT_RELATIONS)


def _ordered_incident_edges(graph: OntologyGraph, node_id: str) -> list[GraphEdge]:
    return sorted(
        graph.incident_edges(node_id),
        key=lambda edge: (
            RELATION_PRIORITY.get(edge.rel, 10),
            edge.source,
            edge.target,
        ),
    )


def _compact_node(node: GraphNode) -> CausalPathNode:
    return CausalPathNode(id=node.id, kind=node.kind, label=node.label)


def _kind_priority(kind: str) -> int:
    priorities = {
        "service": 0,
        "span": 1,
        "endpoint": 2,
        "app": 3,
        "change": 4,
        "ip": 5,
        "metric_series": 6,
        "trace": 7,
    }
    return priorities.get(kind, 20)


def _top_counts(counts: dict[str, int], *, limit: int = 8) -> str:
    return ", ".join(f"{key}={value}" for key, value in Counter(counts).most_common(limit))
