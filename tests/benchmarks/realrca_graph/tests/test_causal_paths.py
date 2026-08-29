from __future__ import annotations

import json
from pathlib import Path

from tests.benchmarks.realrca_graph.causal_paths import build_causal_path_report
from tests.benchmarks.realrca_graph.cli import main
from tests.benchmarks.realrca_graph.models import EvidenceBundle, RootHypothesis


def _graph_context() -> dict:
    nodes = [
        {"id": "case:1", "kind": "case", "label": "case-1", "props": {}},
        {"id": "alarm:a", "kind": "alarm", "label": "alarm-a", "props": {}},
        {"id": "trace:t1", "kind": "trace", "label": "t1", "props": {}},
        {"id": "span:s1", "kind": "span", "label": "slow span", "props": {}},
        {"id": "service:orders", "kind": "service", "label": "orders slow sql", "props": {}},
        {"id": "app:orders", "kind": "app", "label": "orders", "props": {}},
        {"id": "change:c1", "kind": "change", "label": "wide diamond change", "props": {}},
    ]
    for index in range(35):
        nodes.append(
            {"id": f"app:noise-{index}", "kind": "app", "label": f"noise-{index}", "props": {}}
        )
    edges = [
        {"source": "case:1", "rel": "HAS_ALARM", "target": "alarm:a"},
        {"source": "alarm:a", "rel": "RAISED_ON", "target": "app:orders"},
        {"source": "alarm:a", "rel": "MENTIONS", "target": "trace:t1"},
        {"source": "trace:t1", "rel": "HAS_SPAN", "target": "span:s1"},
        {"source": "span:s1", "rel": "INVOKES", "target": "service:orders"},
        {"source": "app:orders", "rel": "HAS_CHANGE", "target": "change:c1"},
    ]
    for index in range(35):
        edges.append(
            {"source": "change:c1", "rel": "CHANGE_TOUCHES", "target": f"app:noise-{index}"}
        )
    return {
        "case": {"case_id": "case-1", "case_type": "TDDL", "data_ref": "alarm-a"},
        "nodes": nodes,
        "edges": edges,
        "evidence": [],
        "root_candidates": [],
        "retrieval_summary": "",
    }


def _bundle() -> EvidenceBundle:
    return EvidenceBundle(
        case_id="case-1",
        split="test",
        case_type="TDDL",
        data_ref="alarm-a",
        ontology=[],
        retrieval_summary="",
        evidence=[],
        hypotheses=[
            RootHypothesis(
                id="h-sql",
                kind="evidence_sql",
                label="orders slow sql",
                root_layer="database",
                score=10.0,
                reason="orders slow sql",
            ),
            RootHypothesis(
                id="h-change",
                kind="change",
                label="wide diamond change",
                root_layer="change",
                score=9.0,
                reason="wide diamond change",
            ),
        ],
    )


def test_causal_path_report_prefers_direct_trace_path_over_high_fanout_change() -> None:
    report = build_causal_path_report(_graph_context(), _bundle())

    assert report.hypotheses[0].hypothesis_id == "h-sql"
    by_id = {item.hypothesis_id: item for item in report.hypotheses}
    assert by_id["h-sql"].path_length is not None
    assert by_id["h-sql"].path_length <= 3
    assert "high_fanout_bridge" in by_id["h-change"].risk_flags
    assert by_id["h-sql"].path_score > by_id["h-change"].path_score


def test_causal_path_uses_endpoint_ownership_and_calls_bridge() -> None:
    graph = {
        "case": {"case_id": "case-bridge", "case_type": "HSF", "data_ref": "alarm-a"},
        "nodes": [
            {"id": "case:bridge", "kind": "case", "label": "case-bridge", "props": {}},
            {"id": "alarm:a", "kind": "alarm", "label": "consumer success rate", "props": {}},
            {"id": "app:consumer", "kind": "app", "label": "consumer", "props": {}},
            {
                "id": "endpoint:consumer:host",
                "kind": "endpoint",
                "label": "consumer:host",
                "props": {},
            },
            {
                "id": "endpoint:provider:host",
                "kind": "endpoint",
                "label": "provider:host",
                "props": {},
            },
            {"id": "trace:t1", "kind": "trace", "label": "t1", "props": {}},
            {"id": "span:s1", "kind": "span", "label": "provider slow span", "props": {}},
        ],
        "edges": [
            {"source": "case:bridge", "rel": "HAS_ALARM", "target": "alarm:a"},
            {"source": "alarm:a", "rel": "RAISED_ON", "target": "app:consumer"},
            {"source": "trace:t1", "rel": "HAS_SPAN", "target": "span:s1"},
            {"source": "span:s1", "rel": "CLIENT", "target": "endpoint:consumer:host"},
            {"source": "span:s1", "rel": "SERVER", "target": "endpoint:provider:host"},
            {
                "source": "endpoint:consumer:host",
                "rel": "CALLS",
                "target": "endpoint:provider:host",
            },
        ],
        "evidence": [],
        "root_candidates": [],
        "retrieval_summary": "",
    }
    bundle = EvidenceBundle(
        case_id="case-bridge",
        split="test",
        case_type="HSF",
        data_ref="alarm-a",
        ontology=[],
        retrieval_summary="",
        evidence=[],
        hypotheses=[
            RootHypothesis(
                id="h-provider",
                kind="endpoint",
                label="provider:host",
                root_layer="service_dependency",
                score=9.0,
                reason="provider timeout",
            )
        ],
    )

    report = build_causal_path_report(graph, bundle, max_depth=5)
    path = report.hypotheses[0]

    assert path.path_score > 0
    assert "no_symptom_path" not in path.risk_flags
    assert "CALLS" in {edge.rel for edge in path.path_edges}
    assert "ENDPOINT_OF" in {edge.rel for edge in path.path_edges}


def test_causal_paths_cli_writes_report(tmp_path: Path) -> None:
    graph = _graph_context()
    graph["root_candidates"] = [
        {
            "kind": "trace_span",
            "label": "orders slow sql",
            "score": 5.0,
            "reason": "abnormal trace span",
            "props": {"trace_id": "t1", "service": "orders slow sql", "duration_ms": 3000},
        }
    ]
    graph_path = tmp_path / "graph_context.json"
    graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    out_json = tmp_path / "paths.json"
    out_md = tmp_path / "paths.md"

    assert (
        main(
            [
                "causal-paths",
                "--graph",
                str(graph_path),
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ]
        )
        == 0
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["case_id"] == "case-1"
    assert "RealRCA Causal Path Audit" in out_md.read_text(encoding="utf-8")
