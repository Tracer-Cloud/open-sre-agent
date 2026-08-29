from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.features import infer_modality, infer_root_layer
from tests.benchmarks.realrca_graph.rds_sql import (
    rds_sql_detail_signals,
    rds_sql_stat_signals,
    summarize_rds_sql,
)


def _matrix_rows() -> dict[str, object]:
    template_a = (
        "SELECT id, user_id FROM `ipm_trade_inventory_4475` `ipm_trade_inventory` "
        "WHERE `item_id` = ?"
    )
    template_b = "SELECT sleep(0.5)"
    return {
        "resultType": "matrix",
        "result": [
            {
                "metric": {
                    "__name__": "avg(cost)",
                    "db": "ali_inv_xcluster_0139",
                    "instance_name": "rm-0pv3dl3f3w28so845",
                    "sql_id": "e3ba429d",
                    "sql_text_template": template_a,
                },
                "values": [[1784876400, "4846411.5882"]],
            },
            {
                "metric": {
                    "__name__": "sum(sql_id_total_time)",
                    "db": "ali_inv_xcluster_0139",
                    "instance_name": "rm-0pv3dl3f3w28so845",
                    "sql_id": "e3ba429d",
                    "sql_text_template": template_a,
                },
                "values": [[1784876400, "3.3158150159E10"]],
            },
            {
                "metric": {
                    "__name__": "sum(execute_count)",
                    "db": "ali_inv_xcluster_0139",
                    "instance_name": "rm-0pv3dl3f3w28so845",
                    "sql_id": "e3ba429d",
                    "sql_text_template": template_a,
                },
                "values": [[1784876400, "6270.0"]],
            },
            {
                "metric": {
                    "__name__": "avg(examined_row_count)",
                    "db": "ali_inv_xcluster_0139",
                    "instance_name": "rm-0pv3dl3f3w28so845",
                    "sql_id": "e3ba429d",
                    "sql_text_template": template_a,
                },
                "values": [[1784876400, "175027.1176"]],
            },
            {
                "metric": {
                    "__name__": "avg(cost)",
                    "db": "cbu_x_manufacture",
                    "instance_name": "rm-8vbn88m2k5j8fu2io",
                    "sql_id": "0X983072351",
                    "sql_text_template": "/* query from idb-toolkit orderId: 32153118 */ "
                    + template_b,
                },
                "values": [[1784876400, "500107.0"]],
            },
        ],
    }


def _stream_rows() -> list[dict[str, object]]:
    detail = {
        "sql_id": "d308643f",
        "db": "skynetcenter_0040",
        "sql_text": "select COUNT(id) from `wm_trade_0040` where store_id = ?",
        "cost": "16636",
        "lock_wait_time": "73",
        "examined_row_count": "76797",
        "user": "app_user",
    }
    return [
        {
            "stream": {"instance_name": "rm-8vbs47nc259kj8z8t"},
            "values": [[1784876400, json.dumps(detail)]],
        }
    ]


def _synthetic_stream_rows() -> list[dict[str, object]]:
    detail = {
        "sql_id": "0X983072351",
        "db": "cbu_x_manufacture",
        "sql_text": "/* query from idb-toolkit orderId: 32153118 */ select sleep(0.5)",
        "cost": "500108",
        "user": "idb_rnd",
    }
    return [
        {
            "stream": {"instance_name": "rm-8vbn88m2k5j8fu2io"},
            "values": [[1784876400, json.dumps(detail)]],
        }
    ]


def test_rds_sql_matrix_rows_are_aggregated_by_sql_id() -> None:
    signals = rds_sql_stat_signals(_matrix_rows())

    assert signals[0].label == "ipm_trade_inventory_4475 e3ba429d slow_sql"
    assert signals[0].props["sql_table"] == "ipm_trade_inventory_4475"
    assert signals[0].props["avg_cost"] == 4846411.5882
    assert signals[0].props["total_time"] == 3.3158150159e10
    assert signals[0].props["execute_count"] == 6270.0
    assert not signals[0].props["synthetic_load"]


def test_rds_sql_synthetic_sleep_query_is_flagged_but_not_top_ranked() -> None:
    signals = rds_sql_stat_signals(_matrix_rows())
    synthetic = next(signal for signal in signals if signal.props["synthetic_load"])

    assert synthetic.label == "0X983072351 slow_sql"
    assert synthetic.score < signals[0].score
    assert "synthetic_sql_load" in synthetic.summary


def test_rds_sql_stream_detail_extracts_cost_lock_and_table() -> None:
    signals = rds_sql_detail_signals(_stream_rows())

    assert signals[0].label == "wm_trade_0040 d308643f slow_sql"
    assert signals[0].props["cost"] == 16636.0
    assert signals[0].props["lock_wait_time"] == 73.0
    assert signals[0].props["examined_rows"] == 76797.0
    assert signals[0].props["user"] == "app_user"


def test_summarize_rds_sql_compacts_top_stat_signal() -> None:
    summary = summarize_rds_sql(_matrix_rows())

    assert "rds_sql count=5" in summary
    assert "ipm_trade_inventory_4475" in summary
    assert "e3ba429d" in summary
    assert "synthetic_sql_load" in summary


def test_empty_rds_sql_result_is_marked_empty() -> None:
    assert summarize_rds_sql({"resultType": "matrix", "result": []}) == "rds_sql count=0 top="


def test_rds_sql_summary_counts_as_sql_modality_and_database_root() -> None:
    summary = summarize_rds_sql(_matrix_rows())

    assert infer_modality("rds_sql_full_rm-0pv3dl3f3w28so845", summary) == "sql"
    assert (
        infer_root_layer("rds_sql_stat", "ipm_trade_inventory_4475 e3ba429d slow_sql", {}, "")
        == "database"
    )


def test_bundle_keeps_nonempty_rds_sql_evidence_and_prefers_rds_support(tmp_path) -> None:
    raw_path = tmp_path / "rds_sql.json"
    raw_path.write_text(json.dumps(_matrix_rows()), encoding="utf-8")
    graph = {
        "case": {"case_id": "case-rds", "split": "validation", "type": "TDDL", "data_ref": "ref"},
        "evidence": [
            {
                "name": "rds_sql_full_rm-0pv3dl3f3w28so845",
                "command": "sf diagnose rds-sql --instance-id rm-0pv3dl3f3w28so845 --type full -f json",
                "returncode": 0,
                "raw_path": str(raw_path),
            }
        ],
        "root_candidates": [
            {
                "kind": "rds_sql_stat",
                "label": "ipm_trade_inventory_4475 e3ba429d slow_sql",
                "score": 4.9,
                "reason": "RDS SQL diagnose reports high-cost SQL near alarm",
            }
        ],
    }

    bundle = build_evidence_bundle(graph)

    assert bundle.evidence[0].modality == "sql"
    assert "ipm_trade_inventory_4475" in bundle.evidence[0].summary
    assert bundle.hypotheses[0].support[0].name == "rds_sql_full_rm-0pv3dl3f3w28so845"


def test_bundle_does_not_promote_synthetic_rds_sql_id_as_fallback_root(tmp_path) -> None:
    raw_path = tmp_path / "rds_sql_synthetic.json"
    raw_path.write_text(json.dumps(_synthetic_stream_rows()), encoding="utf-8")
    graph = {
        "case": {"case_id": "case-rds", "split": "validation", "type": "TDDL", "data_ref": "ref"},
        "evidence": [
            {
                "name": "rds_sql_full_detail_rm-8vbn88m2k5j8fu2io_0X983072351",
                "command": (
                    "sf diagnose rds-sql --instance-id rm-8vbn88m2k5j8fu2io "
                    "--type full --sql-id 0X983072351 -f json"
                ),
                "returncode": 0,
                "raw_path": str(raw_path),
            }
        ],
    }

    bundle = build_evidence_bundle(graph)

    assert bundle.evidence[0].modality == "sql"
    assert "synthetic_sql_load" in bundle.evidence[0].summary
    assert all(hypothesis.kind != "evidence_sql" for hypothesis in bundle.hypotheses)
