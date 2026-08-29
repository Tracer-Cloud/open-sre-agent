from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tests.benchmarks.realrca_graph.graph_store import (
    index_graph_roots,
    index_resolved_graphs,
    search_nodes,
)


def _write_graph(root: Path, case_id: str) -> None:
    graph_dir = root / "test" / case_id
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph_context.json").write_text(
        json.dumps(
            {
                "case": {
                    "split": "test",
                    "case_id": case_id,
                    "data_ref": "snapshot-1",
                    "input": "alarmId=abc",
                    "type": "HSF",
                },
                "nodes": [
                    {
                        "id": "app:consumer",
                        "kind": "app",
                        "label": "consumer-app",
                        "props": {"role": "consumer"},
                    },
                    {
                        "id": "service:com.demo.ProviderApi",
                        "kind": "service",
                        "label": "com.demo.ProviderApi:query",
                    },
                ],
                "edges": [
                    {
                        "source": "app:consumer",
                        "rel": "CALLS",
                        "target": "service:com.demo.ProviderApi",
                    }
                ],
                "evidence": [
                    {
                        "name": "trace_list",
                        "command": "sf trace list",
                        "returncode": 0,
                        "elapsed_sec": 0.1,
                        "summary": "ProviderApi query timeout",
                    }
                ],
                "root_candidates": [
                    {
                        "kind": "trace_span",
                        "label": "com.demo.ProviderApi",
                        "score": 4.5,
                        "reason": "abnormal provider span",
                    }
                ],
                "retrieval_summary": "summary",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_graph_store_indexes_cases_nodes_edges_evidence_and_candidates(tmp_path: Path) -> None:
    root = tmp_path / "graph-v-test"
    db_path = tmp_path / "graphs.sqlite"
    _write_graph(root, "case-1")

    stats = index_graph_roots([root], db_path=db_path, split="test")

    assert stats[0].to_dict() == {
        "graph_label": "graph-v-test",
        "split": "test",
        "case_count": 1,
        "node_count": 2,
        "edge_count": 1,
        "evidence_count": 1,
        "root_candidate_count": 1,
    }
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from cases").fetchone()[0] == 1
        assert conn.execute("select count(*) from nodes").fetchone()[0] == 2
        assert conn.execute("select count(*) from edges").fetchone()[0] == 1
        assert conn.execute("select count(*) from evidence").fetchone()[0] == 1
        assert conn.execute("select count(*) from root_candidates").fetchone()[0] == 1


def test_graph_store_reindex_replaces_same_graph_split(tmp_path: Path) -> None:
    root = tmp_path / "graph-v-test"
    db_path = tmp_path / "graphs.sqlite"
    _write_graph(root, "case-1")

    index_graph_roots([root], db_path=db_path, split="test")
    index_graph_roots([root], db_path=db_path, split="test")

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from cases").fetchone()[0] == 1
        assert conn.execute("select count(*) from nodes").fetchone()[0] == 2
        assert conn.execute("select count(*) from node_tokens").fetchone()[0] > 0


def test_graph_store_search_nodes_by_answer_text(tmp_path: Path) -> None:
    root = tmp_path / "graph-v-test"
    db_path = tmp_path / "graphs.sqlite"
    _write_graph(root, "case-1")
    index_graph_roots([root], db_path=db_path, split="test")

    matches = search_nodes(
        "ProviderApi query timeout",
        db_path=db_path,
        split="test",
        case_id="case-1",
        kinds=["service"],
    )

    assert matches
    assert matches[0].node_id == "service:com.demo.ProviderApi"
    assert matches[0].overlap >= 2


def test_graph_store_resolved_index_keeps_first_graph_per_case(tmp_path: Path) -> None:
    older = tmp_path / "graph-v-old"
    newer = tmp_path / "graph-v-new"
    db_path = tmp_path / "graphs.sqlite"
    _write_graph(older, "case-1")
    _write_graph(newer, "case-1")
    _write_graph(older, "case-2")

    stats = index_resolved_graphs(
        [newer, older],
        graph_label="latest-test-resolved",
        db_path=db_path,
        split="test",
    )

    assert stats.case_count == 2
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "select case_id, graph_path from cases where graph_label = ? order by case_id",
            ("latest-test-resolved",),
        ).fetchall()
    assert rows[0][0] == "case-1"
    assert "graph-v-new" in rows[0][1]
    assert rows[1][0] == "case-2"
    assert "graph-v-old" in rows[1][1]


def test_graph_store_indexes_derived_pattern_roots(tmp_path: Path) -> None:
    root = tmp_path / "graph-v-test"
    graph_dir = root / "test" / "case-1"
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph_context.json").write_text(
        json.dumps(
            {
                "case": {
                    "split": "test",
                    "case_id": "case-1",
                    "data_ref": "snapshot-1",
                    "input": "机器存活数量同比下跌 appGroup=mtee3.cn.prodhost",
                    "type": "OTHER",
                },
                "nodes": [],
                "edges": [],
                "evidence": [
                    {
                        "name": "event_change_list",
                        "command": "sf event change list --app mtee3 --infra -f json",
                        "returncode": 0,
                        "summary": {
                            "business_changes": [
                                {
                                    "id": "2843585453",
                                    "change_type": "OFFLINE_HOST",
                                    "title": "正式-机器下线",
                                    "result": "变更成功",
                                    "system": "normandy-director",
                                    "end_time": "2026-06-11 22:20:36",
                                }
                            ]
                        },
                    }
                ],
                "root_candidates": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "graphs.sqlite"

    stats = index_graph_roots([root], db_path=db_path, split="test")

    assert stats[0].root_candidate_count == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "select kind, label, props_json from root_candidates where case_id = ?",
            ("case-1",),
        ).fetchone()
    assert row[0] == "pattern_instance_count_drop_offline_change"
    assert row[1] == "mtee3 change_id=2843585453 normandy_offline_capacity_drop"
    assert json.loads(row[2])["derived_from"] == "pattern_root_candidates"


def test_graph_store_uses_raw_path_for_derived_pattern_roots(tmp_path: Path) -> None:
    root = tmp_path / "graph-v-test"
    graph_dir = root / "test" / "case-1"
    raw_dir = graph_dir / "raw"
    raw_dir.mkdir(parents=True)
    raw_path = raw_dir / "event_change_list.json"
    raw_path.write_text(
        json.dumps(
            {
                "business_changes": [
                    {
                        "id": "2843585453",
                        "change_type": "OFFLINE_HOST",
                        "title": "正式-机器下线",
                        "result": "变更成功",
                        "system": "normandy-director",
                        "end_time": "2026-06-11 22:20:36",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (graph_dir / "graph_context.json").write_text(
        json.dumps(
            {
                "case": {
                    "split": "test",
                    "case_id": "case-1",
                    "data_ref": "snapshot-1",
                    "input": "机器存活数量同比下跌 appGroup=mtee3.cn.prodhost",
                    "type": "OTHER",
                },
                "nodes": [],
                "edges": [],
                "evidence": [
                    {
                        "name": "event_change_list",
                        "command": "sf event change list --app mtee3 --infra -f json",
                        "returncode": 0,
                        "summary": '{"business_changes":[',
                        "raw_path": str(raw_path),
                    }
                ],
                "root_candidates": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "graphs.sqlite"

    index_graph_roots([root], db_path=db_path, split="test")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "select kind, label from root_candidates where case_id = ?",
            ("case-1",),
        ).fetchone()
    assert row == (
        "pattern_instance_count_drop_offline_change",
        "mtee3 change_id=2843585453 normandy_offline_capacity_drop",
    )
