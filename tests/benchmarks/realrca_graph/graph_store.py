from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.features import token_features
from tests.benchmarks.realrca_graph.io import REALRCA_GRAPH
from tests.benchmarks.realrca_graph.root_patterns import pattern_root_candidates
from tests.benchmarks.realrca_graph.summary_cache import compact_evidence_summary_cached

DEFAULT_GRAPH_DB = REALRCA_GRAPH / "realrca_case_graphs.sqlite"
SEARCH_TOKEN_RE = re.compile(r"[a-zA-Z0-9_.$:-]{3,}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    graph_label TEXT NOT NULL,
    split TEXT NOT NULL,
    case_id TEXT NOT NULL,
    case_type TEXT,
    data_ref TEXT,
    input TEXT,
    graph_path TEXT NOT NULL,
    node_count INTEGER NOT NULL,
    edge_count INTEGER NOT NULL,
    candidate_count INTEGER NOT NULL,
    evidence_count INTEGER NOT NULL,
    retrieval_summary TEXT,
    PRIMARY KEY (graph_label, split, case_id)
);
CREATE TABLE IF NOT EXISTS nodes (
    graph_label TEXT NOT NULL,
    split TEXT NOT NULL,
    case_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    props_json TEXT NOT NULL,
    PRIMARY KEY (graph_label, split, case_id, node_id)
);
CREATE TABLE IF NOT EXISTS node_tokens (
    graph_label TEXT NOT NULL,
    split TEXT NOT NULL,
    case_id TEXT NOT NULL,
    token TEXT NOT NULL,
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    PRIMARY KEY (graph_label, split, case_id, token, node_id)
);
CREATE TABLE IF NOT EXISTS edges (
    graph_label TEXT NOT NULL,
    split TEXT NOT NULL,
    case_id TEXT NOT NULL,
    source TEXT NOT NULL,
    rel TEXT NOT NULL,
    target TEXT NOT NULL,
    props_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    graph_label TEXT NOT NULL,
    split TEXT NOT NULL,
    case_id TEXT NOT NULL,
    name TEXT NOT NULL,
    command TEXT,
    returncode INTEGER,
    elapsed_sec REAL,
    raw_path TEXT,
    summary TEXT,
    parse_error TEXT
);
CREATE TABLE IF NOT EXISTS root_candidates (
    graph_label TEXT NOT NULL,
    split TEXT NOT NULL,
    case_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    score REAL NOT NULL,
    reason TEXT,
    props_json TEXT NOT NULL,
    PRIMARY KEY (graph_label, split, case_id, rank)
);
CREATE INDEX IF NOT EXISTS idx_cases_case
    ON cases(split, case_id, graph_label);
CREATE INDEX IF NOT EXISTS idx_nodes_kind
    ON nodes(graph_label, split, kind, label);
CREATE INDEX IF NOT EXISTS idx_node_tokens
    ON node_tokens(split, case_id, token);
CREATE INDEX IF NOT EXISTS idx_edges_rel
    ON edges(graph_label, split, rel);
CREATE INDEX IF NOT EXISTS idx_candidates_kind_score
    ON root_candidates(graph_label, split, kind, score DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_name
    ON evidence(graph_label, split, name);
"""


@dataclass(frozen=True)
class GraphIndexStats:
    """Indexing counts for one graph label and split."""

    graph_label: str
    split: str
    case_count: int
    node_count: int
    edge_count: int
    evidence_count: int
    root_candidate_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NodeMatch:
    """A graph-store node hit for free-text entity retrieval."""

    graph_label: str
    split: str
    case_id: str
    node_id: str
    kind: str
    label: str
    overlap: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def initialize_graph_store(db_path: Path = DEFAULT_GRAPH_DB) -> None:
    """Create the graph-store schema if needed."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)


def index_graph_roots(
    graph_roots: Sequence[Path],
    *,
    db_path: Path = DEFAULT_GRAPH_DB,
    split: str = "test",
) -> list[GraphIndexStats]:
    """Index graph_context files from graph roots into SQLite."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        stats: list[GraphIndexStats] = []
        for root in graph_roots:
            stats.append(_index_graph_root(conn, root, split=split))
        conn.commit()
    return stats


def index_resolved_graphs(
    graph_roots: Sequence[Path],
    *,
    graph_label: str,
    db_path: Path = DEFAULT_GRAPH_DB,
    split: str = "test",
) -> GraphIndexStats:
    """Index the first available graph_context per case from ordered graph roots."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _delete_graph_split(conn, graph_label, split)
        case_count = 0
        node_count = 0
        edge_count = 0
        evidence_count = 0
        root_candidate_count = 0
        for graph_path in resolved_graph_context_paths(graph_roots, split=split):
            counts = _index_graph_context(conn, graph_label, split, graph_path)
            case_count += 1
            node_count += counts["nodes"]
            edge_count += counts["edges"]
            evidence_count += counts["evidence"]
            root_candidate_count += counts["root_candidates"]
        conn.commit()
    return GraphIndexStats(
        graph_label=graph_label,
        split=split,
        case_count=case_count,
        node_count=node_count,
        edge_count=edge_count,
        evidence_count=evidence_count,
        root_candidate_count=root_candidate_count,
    )


def resolved_graph_context_paths(graph_roots: Sequence[Path], *, split: str = "test") -> list[Path]:
    """Return one graph_context path per case, honoring graph-root priority order."""

    output: list[Path] = []
    seen: set[str] = set()
    for root in graph_roots:
        for graph_path in sorted((root / split).glob("*/graph_context.json")):
            case_id = graph_path.parent.name
            if case_id in seen:
                continue
            seen.add(case_id)
            output.append(graph_path)
    return output


def search_nodes(
    text: Any,
    *,
    db_path: Path = DEFAULT_GRAPH_DB,
    split: str = "test",
    case_id: str | None = None,
    graph_labels: Sequence[str] = (),
    kinds: Sequence[str] = (),
    limit: int = 20,
) -> list[NodeMatch]:
    """Find case graph nodes whose tokenized label/properties overlap text."""

    tokens = sorted(_search_tokens(text))
    if not tokens:
        return []
    params: list[Any] = [split, *tokens]
    where = [f"split = ? AND token IN ({','.join('?' for _ in tokens)})"]
    if case_id:
        where.append("case_id = ?")
        params.append(case_id)
    if graph_labels:
        where.append(f"graph_label IN ({','.join('?' for _ in graph_labels)})")
        params.extend(graph_labels)
    if kinds:
        where.append(f"kind IN ({','.join('?' for _ in kinds)})")
        params.extend(kinds)

    query = f"""
        SELECT graph_label, split, case_id, node_id, kind, label, COUNT(DISTINCT token) AS overlap
        FROM node_tokens
        WHERE {" AND ".join(where)}
        GROUP BY graph_label, split, case_id, node_id, kind, label
        ORDER BY overlap DESC, kind, label
        LIMIT ?
    """
    params.append(limit)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        NodeMatch(
            graph_label=str(row[0]),
            split=str(row[1]),
            case_id=str(row[2]),
            node_id=str(row[3]),
            kind=str(row[4]),
            label=str(row[5]),
            overlap=int(row[6]),
        )
        for row in rows
    ]


def _index_graph_root(conn: sqlite3.Connection, root: Path, *, split: str) -> GraphIndexStats:
    graph_label = root.name
    _delete_graph_split(conn, graph_label, split)
    case_count = 0
    node_count = 0
    edge_count = 0
    evidence_count = 0
    root_candidate_count = 0
    for graph_path in sorted((root / split).glob("*/graph_context.json")):
        counts = _index_graph_context(conn, graph_label, split, graph_path)
        case_count += 1
        node_count += counts["nodes"]
        edge_count += counts["edges"]
        evidence_count += counts["evidence"]
        root_candidate_count += counts["root_candidates"]
    return GraphIndexStats(
        graph_label=graph_label,
        split=split,
        case_count=case_count,
        node_count=node_count,
        edge_count=edge_count,
        evidence_count=evidence_count,
        root_candidate_count=root_candidate_count,
    )


def _delete_graph_split(conn: sqlite3.Connection, graph_label: str, split: str) -> None:
    for table in ("cases", "nodes", "node_tokens", "edges", "evidence", "root_candidates"):
        conn.execute(
            f"DELETE FROM {table} WHERE graph_label = ? AND split = ?",
            (graph_label, split),
        )


def _index_graph_context(
    conn: sqlite3.Connection,
    graph_label: str,
    split: str,
    graph_path: Path,
) -> dict[str, int]:
    context = json.loads(graph_path.read_text(encoding="utf-8"))
    case = _case_payload(context, split=split, graph_path=graph_path)
    case_id = case["case_id"]
    nodes = [node for node in context.get("nodes") or [] if isinstance(node, dict)]
    edges = [edge for edge in context.get("edges") or [] if isinstance(edge, dict)]
    evidence = [item for item in context.get("evidence") or [] if isinstance(item, dict)]
    root_candidates = [
        item for item in context.get("root_candidates") or [] if isinstance(item, dict)
    ]
    indexed_root_candidates = _root_candidates_with_patterns(context, root_candidates, evidence)

    conn.execute(
        """
        INSERT OR REPLACE INTO cases (
            graph_label, split, case_id, case_type, data_ref, input, graph_path,
            node_count, edge_count, candidate_count, evidence_count, retrieval_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            graph_label,
            split,
            case_id,
            str(case.get("type") or case.get("case_type") or ""),
            str(case.get("data_ref") or ""),
            str(case.get("input") or ""),
            str(graph_path),
            len(nodes),
            len(edges),
            len(indexed_root_candidates),
            len(evidence),
            str(context.get("retrieval_summary") or ""),
        ),
    )
    _insert_nodes(conn, graph_label, split, case_id, nodes)
    _insert_edges(conn, graph_label, split, case_id, edges)
    _insert_evidence(conn, graph_label, split, case_id, evidence)
    _insert_root_candidates(conn, graph_label, split, case_id, indexed_root_candidates)
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "evidence": len(evidence),
        "root_candidates": len(indexed_root_candidates),
    }


def _root_candidates_with_patterns(
    context: dict[str, Any],
    root_candidates: Sequence[dict[str, Any]],
    evidence: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = list(root_candidates)
    seen = {
        (str(item.get("kind") or ""), str(item.get("label") or ""))
        for item in output
        if isinstance(item, dict)
    }
    for item in pattern_root_candidates(_compact_pattern_context(context, evidence)):
        key = (str(item.get("kind") or ""), str(item.get("label") or ""))
        if key in seen:
            continue
        props = item.get("props") if isinstance(item.get("props"), dict) else {}
        output.append({**item, "props": {**props, "derived_from": "pattern_root_candidates"}})
        seen.add(key)
    return output


def _compact_pattern_context(
    context: dict[str, Any],
    evidence: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "case": context.get("case"),
        "retrieval_summary": context.get("retrieval_summary"),
        "root_candidates": context.get("root_candidates"),
        "evidence": [
            {
                "name": item.get("name"),
                "command": item.get("command"),
                "summary": compact_evidence_summary_cached(
                    str(item.get("name") or ""),
                    str(item.get("command") or ""),
                    str(item.get("raw_path") or item.get("raw_ref") or ""),
                    item.get("summary") or item,
                ),
            }
            for item in evidence
        ],
    }


def _case_payload(context: dict[str, Any], *, split: str, graph_path: Path) -> dict[str, Any]:
    case = context.get("case")
    if isinstance(case, dict) and isinstance(case.get("case_id"), str):
        return case
    for node in context.get("nodes") or []:
        if not isinstance(node, dict) or node.get("kind") != "case":
            continue
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        case_id = str(node.get("label") or node.get("id") or graph_path.parent.name)
        return {"case_id": case_id, "split": split, **props}
    return {"case_id": graph_path.parent.name, "split": split}


def _insert_nodes(
    conn: sqlite3.Connection,
    graph_label: str,
    split: str,
    case_id: str,
    nodes: Iterable[dict[str, Any]],
) -> None:
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        kind = str(node.get("kind") or "")
        label = str(node.get("label") or node_id)
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        props_json = _json_dumps(props)
        conn.execute(
            """
            INSERT OR REPLACE INTO nodes (
                graph_label, split, case_id, node_id, kind, label, props_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (graph_label, split, case_id, node_id, kind, label, props_json),
        )
        token_payload = _node_token_payload(node_id, kind, label, props)
        for token in _search_tokens(token_payload):
            conn.execute(
                """
                INSERT OR IGNORE INTO node_tokens (
                    graph_label, split, case_id, token, node_id, kind, label
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (graph_label, split, case_id, token, node_id, kind, label),
            )


def _node_token_payload(
    node_id: str, kind: str, label: str, props: dict[str, Any]
) -> dict[str, str]:
    props_preview = _json_dumps(props)
    if len(props_preview) > 1200:
        props_preview = props_preview[:1200]
    return {
        "id": node_id,
        "kind": kind,
        "label": label,
        "props": props_preview,
    }


def _insert_edges(
    conn: sqlite3.Connection,
    graph_label: str,
    split: str,
    case_id: str,
    edges: Iterable[dict[str, Any]],
) -> None:
    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target:
            continue
        props = edge.get("props") if isinstance(edge.get("props"), dict) else {}
        conn.execute(
            """
            INSERT INTO edges (
                graph_label, split, case_id, source, rel, target, props_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                graph_label,
                split,
                case_id,
                source,
                str(edge.get("rel") or ""),
                target,
                _json_dumps(props),
            ),
        )


def _insert_evidence(
    conn: sqlite3.Connection,
    graph_label: str,
    split: str,
    case_id: str,
    evidence: Iterable[dict[str, Any]],
) -> None:
    for item in evidence:
        conn.execute(
            """
            INSERT INTO evidence (
                graph_label, split, case_id, name, command, returncode,
                elapsed_sec, raw_path, summary, parse_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                graph_label,
                split,
                case_id,
                str(item.get("name") or ""),
                str(item.get("command") or ""),
                item.get("returncode"),
                item.get("elapsed_sec"),
                str(item.get("raw_path") or ""),
                str(item.get("summary") or ""),
                str(item.get("parse_error") or ""),
            ),
        )


def _insert_root_candidates(
    conn: sqlite3.Connection,
    graph_label: str,
    split: str,
    case_id: str,
    root_candidates: Sequence[dict[str, Any]],
) -> None:
    for rank, item in enumerate(root_candidates, start=1):
        props = item.get("props") if isinstance(item.get("props"), dict) else {}
        conn.execute(
            """
            INSERT OR REPLACE INTO root_candidates (
                graph_label, split, case_id, rank, kind, label, score, reason, props_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                graph_label,
                split,
                case_id,
                rank,
                str(item.get("kind") or ""),
                str(item.get("label") or ""),
                _float(item.get("score")),
                str(item.get("reason") or ""),
                _json_dumps(props),
            ),
        )


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _search_tokens(value: Any) -> set[str]:
    tokens = set(token_features(value))
    text = _json_dumps(value) if not isinstance(value, str) else value
    for raw in SEARCH_TOKEN_RE.findall(text):
        normalized = raw.strip(" .:$").lower()
        if normalized:
            tokens.add(f"term:{normalized}")
        for fragment in re.split(r"[^a-zA-Z0-9_]+", raw):
            if len(fragment) >= 3:
                tokens.add(f"term:{fragment.lower()}")
    return tokens


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
