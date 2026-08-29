from __future__ import annotations

import re
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from tests.benchmarks.realrca_graph.features import text_for_features, token_features

SEARCH_TOKEN_RE = re.compile(r"[a-zA-Z0-9_.$:-]{3,}")


@dataclass(frozen=True)
class GraphNode:
    """One ontology node from a RealRCA graph_context."""

    id: str
    kind: str
    label: str
    props: dict[str, Any] = field(default_factory=dict)

    def text(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label, "props": self.props}


@dataclass(frozen=True)
class GraphEdge:
    """One typed relation between ontology nodes."""

    source: str
    rel: str
    target: str
    props: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Neighborhood:
    """A bounded graph neighborhood around selected seed nodes."""

    seed_ids: list[str]
    node_ids: list[str]
    edges: list[GraphEdge]


@dataclass(frozen=True)
class NodeHit:
    """A node matched by text tokens."""

    node: GraphNode
    overlap: int


class OntologyGraph:
    """Queryable in-memory view of graph_context nodes and edges."""

    def __init__(self, nodes: Iterable[GraphNode], edges: Iterable[GraphEdge]) -> None:
        self.nodes = {node.id: node for node in nodes}
        self.edges = list(edges)
        self._outgoing: dict[str, list[GraphEdge]] = defaultdict(list)
        self._incoming: dict[str, list[GraphEdge]] = defaultdict(list)
        self._by_kind: dict[str, list[GraphNode]] = defaultdict(list)
        self._token_index: dict[str, set[str]] = defaultdict(set)

        for node in self.nodes.values():
            self._by_kind[node.kind].append(node)
            for token in _search_tokens(node.text()):
                self._token_index[token].add(node.id)
        for edge in self.edges:
            self._outgoing[edge.source].append(edge)
            self._incoming[edge.target].append(edge)

    @classmethod
    def from_context(cls, graph_context: dict[str, Any]) -> OntologyGraph:
        nodes = []
        for raw in graph_context.get("nodes") or []:
            if not isinstance(raw, dict):
                continue
            node_id = str(raw.get("id") or "")
            if not node_id:
                continue
            props = raw.get("props") if isinstance(raw.get("props"), dict) else {}
            nodes.append(
                GraphNode(
                    id=node_id,
                    kind=str(raw.get("kind") or ""),
                    label=str(raw.get("label") or node_id),
                    props=props,
                )
            )

        edges = []
        for raw in graph_context.get("edges") or []:
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("source") or "")
            target = str(raw.get("target") or "")
            if not source or not target:
                continue
            props = raw.get("props") if isinstance(raw.get("props"), dict) else {}
            edges.append(
                GraphEdge(
                    source=source,
                    rel=str(raw.get("rel") or ""),
                    target=target,
                    props=props,
                )
            )
        return cls(nodes, _with_inferred_endpoint_app_edges(nodes, edges))

    def nodes_by_kind(self, kind: str) -> list[GraphNode]:
        return list(self._by_kind.get(kind, []))

    def incident_edges(self, node_id: str, *, rel: str | None = None) -> list[GraphEdge]:
        edges = self._outgoing.get(node_id, []) + self._incoming.get(node_id, [])
        if rel is not None:
            return [edge for edge in edges if edge.rel == rel]
        return list(edges)

    def node_ids_for_text(self, value: Any) -> list[str]:
        matched: set[str] = set()
        for token in _search_tokens(value):
            matched.update(self._token_index.get(token, set()))
        return sorted(matched)

    def node_hits_for_text(
        self,
        value: Any,
        *,
        kinds: Iterable[str] = (),
        limit: int = 20,
    ) -> list[NodeHit]:
        wanted_kinds = set(kinds)
        counts: dict[str, set[str]] = defaultdict(set)
        for token in _search_tokens(value):
            for node_id in self._token_index.get(token, set()):
                node = self.nodes.get(node_id)
                if node is None:
                    continue
                if wanted_kinds and node.kind not in wanted_kinds:
                    continue
                counts[node_id].add(token)
        hits = [
            NodeHit(node=self.nodes[node_id], overlap=len(tokens))
            for node_id, tokens in counts.items()
        ]
        hits.sort(key=lambda item: (-item.overlap, item.node.kind, item.node.label))
        return hits[:limit]

    def neighborhood(self, seed_ids: Iterable[str], *, depth: int = 1) -> Neighborhood:
        seeds = [node_id for node_id in seed_ids if node_id in self.nodes]
        seen_nodes = set(seeds)
        seen_edges: dict[tuple[str, str, str], GraphEdge] = {}
        queue = deque((node_id, 0) for node_id in seeds)

        while queue:
            node_id, distance = queue.popleft()
            if distance >= depth:
                continue
            for edge in self.incident_edges(node_id):
                key = (edge.source, edge.rel, edge.target)
                seen_edges[key] = edge
                for neighbor_id in (edge.source, edge.target):
                    if neighbor_id in seen_nodes or neighbor_id not in self.nodes:
                        continue
                    seen_nodes.add(neighbor_id)
                    queue.append((neighbor_id, distance + 1))

        return Neighborhood(
            seed_ids=seeds,
            node_ids=sorted(seen_nodes),
            edges=sorted(
                seen_edges.values(), key=lambda edge: (edge.source, edge.rel, edge.target)
            ),
        )


def _search_tokens(value: Any) -> set[str]:
    tokens = set(token_features(value))
    text = value if isinstance(value, str) else text_for_features(value)
    for raw in SEARCH_TOKEN_RE.findall(text):
        normalized = raw.strip(" .:$").lower()
        if normalized:
            tokens.add(f"term:{normalized}")
        for fragment in re.split(r"[^a-zA-Z0-9_]+", raw):
            if len(fragment) >= 3:
                tokens.add(f"term:{fragment.lower()}")
    return tokens


def _with_inferred_endpoint_app_edges(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> list[GraphEdge]:
    existing = {(edge.source, edge.rel, edge.target) for edge in edges}
    app_by_name: dict[str, str] = {}
    for node in nodes:
        if node.kind != "app":
            continue
        for name in _app_names(node):
            app_by_name.setdefault(name, node.id)

    inferred = list(edges)
    for node in nodes:
        if node.kind != "endpoint":
            continue
        for name in _endpoint_app_names(node):
            app_id = app_by_name.get(name)
            if app_id is None:
                continue
            key = (node.id, "ENDPOINT_OF", app_id)
            if key in existing:
                continue
            existing.add(key)
            inferred.append(GraphEdge(source=node.id, rel="ENDPOINT_OF", target=app_id))
            break
    return inferred


def _app_names(node: GraphNode) -> set[str]:
    names = {_clean_entity_name(node.id), _clean_entity_name(node.label)}
    return {name for name in names if name}


def _endpoint_app_names(node: GraphNode) -> list[str]:
    names = [
        _clean_entity_name(node.id.removeprefix("endpoint:")),
        _clean_entity_name(node.label),
    ]
    return list(dict.fromkeys(name for name in names if name))


def _clean_entity_name(value: str) -> str:
    text = str(value or "").lower().strip()
    if text.startswith(("http://", "https://")):
        return ""
    text = text.removeprefix("app:").removeprefix("endpoint:")
    for separator in (":", "@", "#", "/", "?"):
        text = text.split(separator, 1)[0]
    return text.strip(" .:_")
