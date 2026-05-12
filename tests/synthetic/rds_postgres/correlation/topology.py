from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TopologyNode:
    name: str
    node_type: str
    upstream_of: tuple[str, ...]


@dataclass(frozen=True)
class TopologyCorrelation:
    source: str
    target: str
    adjacency_score: float
    rationale: str


def score_topology_adjacency(
    *,
    source: TopologyNode,
    target: TopologyNode,
) -> TopologyCorrelation:
    directly_connected = target.name in source.upstream_of

    score = 1.0 if directly_connected else 0.0

    rationale = (
        f"{source.name} is directly upstream of {target.name}."
        if directly_connected
        else f"{source.name} has no direct topology adjacency to {target.name}."
    )

    return TopologyCorrelation(
        source=source.name,
        target=target.name,
        adjacency_score=score,
        rationale=rationale,
    )
