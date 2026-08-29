from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.trajectory_evidence import augment_graph_context_with_trajectory


def test_augment_graph_context_adds_app_log_signal_from_dma_tool_result() -> None:
    rows = [
        {
            "extendMeta": {"__LogStore__": "application", "__Project__": "ae-linehaul-wms"},
            "logItem": {
                "content": (
                    'BigBagWideDetailMgrProcessor search, request: {"pagination":{"pageNo":3,'
                    '"pageSize":500},"query":{"inboundBatchCode":{"$in":["A","B","C","D",'
                    '"E","F","G","H","I"]}},"userContext":{"requestUri":'
                    '"/api/method/main/bigBagWideDetailMgr/export","warehouseCode":"W1"}}'
                )
            },
            "sourceMeta": {"__source__": "11.183.88.5"},
        }
    ]
    run_payload = {
        "output": [
            {
                "event": "agent.tool_use",
                "data": {
                    "arguments": [
                        {
                            "id": "toolu_1",
                            "input": {"command": "sf log sls query --query export -f json"},
                        }
                    ]
                },
            },
            {
                "event": "agent.tool_result",
                "data": {
                    "result": [
                        {
                            "tool_use_id": "toolu_1",
                            "content": json.dumps(rows, ensure_ascii=False),
                        }
                    ]
                },
            },
        ]
    }

    augmented = augment_graph_context_with_trajectory(
        {"case": {"case_id": "case-1"}, "evidence": [], "root_candidates": []},
        run_payload,
        source="dma_v83",
    )

    assert augmented["root_candidates"][0]["kind"] == "heavy_business_query"
    assert augmented["root_candidates"][0]["label"] == (
        "heavy_query:/api/method/main/bigBagWideDetailMgr/export:pageSize=500:in=9"
    )
    assert augmented["root_candidates"][0]["props"]["source"] == "dma_v83"
    assert augmented["evidence"][0]["name"] == "dma_v83_sls_app_heavy_business_query_1"
    assert augmented["evidence"][0]["command"] == "sf log sls query --query export -f json"


def test_augment_graph_context_adds_sql_log_signal_from_dma_tool_result() -> None:
    rows = [
        {
            "logItem": {
                "content": (
                    "ERR-CODE: [TDDL-4614][ERR_EXECUTE_ON_MYSQL] Error occurs when execute "
                    "on GROUP 'ALIBABA_MANHATTAN_GROUP' ATOM 'atom_1': Duplicate entry "
                    "'lock-a' for key 'WS_GENERATE_LOCK_UK' ### SQL: insert into "
                    "WS_GENERATE_LOCK (ID, NAME) values (?, ?) eagleEyeId=211b268417804962111914664d0f17"
                )
            },
            "sourceMeta": {"__source__": "33.27.38.132"},
        }
    ]
    run_payload = _run_payload(
        "sf log sls query --query TDDL-4614 -f json",
        rows,
    )

    augmented = augment_graph_context_with_trajectory(
        {"case": {"case_id": "case-1"}, "evidence": [], "root_candidates": []},
        run_payload,
        source="dma_sql",
    )

    assert augmented["root_candidates"][0]["kind"] == "sql_log_error"
    assert (
        augmented["root_candidates"][0]["label"] == "TDDL-4614:WS_GENERATE_LOCK:WS_GENERATE_LOCK_UK"
    )
    assert augmented["root_candidates"][0]["props"]["trace_ids"] == [
        "211b268417804962111914664d0f17"
    ]
    assert augmented["evidence"][0]["name"] == "dma_sql_sls_sql_sql_log_error_1"
    assert "trajectory_sls_sql_signal" in augmented["evidence"][0]["summary"]


def test_augment_graph_context_adds_access_log_signal_from_dma_tool_result() -> None:
    rows = [
        {
            "logItem": {
                "status": "401",
                "request_method": "GET",
                "eagleeye_traceid": "8ccd75d217815846928741544e77e6",
                "request_uri": "/gocFaultDef/innerApi/v2/incident/scenarios/level/defs",
            },
            "sourceMeta": {"__source__": "33.102.22.35"},
        }
    ]
    run_payload = _run_payload(
        "sf log sls query --query gocFaultDef -f json",
        rows,
    )

    augmented = augment_graph_context_with_trajectory(
        {"case": {"case_id": "case-1"}, "evidence": [], "root_candidates": []},
        run_payload,
        source="dma_access",
    )

    assert augmented["root_candidates"][0]["kind"] == "http_access_error"
    assert augmented["root_candidates"][0]["props"]["auth_failure"] is True
    assert augmented["root_candidates"][0]["props"]["trace_ids"] == [
        "8ccd75d217815846928741544e77e6"
    ]
    assert augmented["evidence"][0]["name"] == "dma_access_sls_access_http_access_error_1"


def test_augment_graph_context_adds_rds_sql_signal_from_dma_tool_result() -> None:
    records = {
        "resultType": "matrix",
        "result": [
            {
                "metric": {
                    "__name__": "avg(cost)",
                    "db": "ali_inv_xcluster_0139",
                    "instance_name": "rm-0pv3dl3f3w28so845",
                    "sql_id": "e3ba429d",
                    "sql_text_template": "SELECT id FROM `ipm_trade_inventory_4475` WHERE item_id = ?",
                },
                "values": [[1784876400, "4846411.5882"]],
            }
        ],
    }
    run_payload = _run_payload(
        "sf diagnose rds-sql --instance-id rm-0pv3dl3f3w28so845 --type full -f json",
        records,
    )

    augmented = augment_graph_context_with_trajectory(
        {"case": {"case_id": "case-1"}, "evidence": [], "root_candidates": []},
        run_payload,
        source="dma_rds",
    )

    assert augmented["root_candidates"][0]["kind"] == "rds_sql_stat"
    assert augmented["root_candidates"][0]["label"] == "ipm_trade_inventory_4475 e3ba429d slow_sql"
    assert augmented["root_candidates"][0]["props"]["sql_table"] == "ipm_trade_inventory_4475"
    assert augmented["evidence"][0]["name"] == "dma_rds_rds_sql_rds_sql_stat_1"


def test_augment_graph_context_adds_stale_db_signal_from_jsonl_tool_result() -> None:
    rows = [
        {
            "time": "2026-08-19 09:39:12",
            "source": "33.44.96.71",
            "class": "AccountBalanceDateUpdateProcessor",
            "method": "process",
            "errorCode": "EXCEPTION_ERROR",
            "errorMessage": (
                "### Error querying database. Cause: "
                "com.mysql.jdbc.exceptions.jdbc4.CommunicationsException: "
                "Communications link failureThe last packet successfully received "
                "from the server was 176,503 millisecon"
            ),
            "traceId": "213dd8f217871035517475239d0d4d",
            "result": "false",
        },
        {
            "time": "2026-08-19 09:39:17",
            "source": "11.82.10.42",
            "class": "AccountBalanceDateUpdateProcessor",
            "method": "process",
            "errorCode": "EXCEPTION_ERROR",
            "errorMessage": (
                "### Error querying database. Cause: "
                "com.mysql.jdbc.exceptions.jdbc4.CommunicationsException: "
                "Communications link failureThe last packet successfully received "
                "from the server was 171,163 millisecon"
            ),
            "traceId": "211b269817871035564851926d0e51",
            "result": "false",
        },
    ]
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    run_payload = _run_payload_with_content(
        "sf log sls query --query 'EXCEPTION_ERROR AND Communications' -f json",
        content,
    )

    augmented = augment_graph_context_with_trajectory(
        {"case": {"case_id": "case-1"}, "evidence": [], "root_candidates": []},
        run_payload,
        source="dma_jsonl",
    )

    assert augmented["root_candidates"][0]["kind"] == "stale_db_connection"
    assert augmented["root_candidates"][0]["label"] == "stale_jdbc_connection:mysql"
    assert augmented["root_candidates"][0]["props"]["stale_packet_ms"] == ["176503", "171163"]
    assert augmented["root_candidates"][0]["props"]["trace_ids"] == [
        "213dd8f217871035517475239d0d4d",
        "211b269817871035564851926d0e51",
    ]
    assert augmented["evidence"][0]["name"] == "dma_jsonl_sls_app_stale_db_connection_1"


def test_augment_graph_context_keeps_richer_duplicate_trajectory_signal() -> None:
    sparse_rows = [
        {
            "source": "33.44.96.71",
            "error": "### Cause: com.mysql.jdbc.exceptions.jdbc4.CommunicationsException: Communications link failure",
        }
    ]
    rich_rows = [
        {
            "source": "33.44.96.71",
            "errorMessage": (
                "### Cause: com.mysql.jdbc.exceptions.jdbc4.CommunicationsException: "
                "Communications link failureThe last packet successfully received "
                "from the server was 176,503 millisecon"
            ),
            "traceId": "213dd8f217871035517475239d0d4d",
        }
    ]
    run_payload = _run_payload_pairs(
        [
            ("toolu_sparse", "sf log sls query --query Communications -f json", sparse_rows),
            ("toolu_rich", "sf log sls query --query EXCEPTION_ERROR -f json", rich_rows),
        ]
    )

    augmented = augment_graph_context_with_trajectory(
        {"case": {"case_id": "case-1"}, "evidence": [], "root_candidates": []},
        run_payload,
        source="dma_jsonl",
    )

    assert len(augmented["root_candidates"]) == 1
    assert augmented["root_candidates"][0]["props"]["trace_ids"] == [
        "213dd8f217871035517475239d0d4d"
    ]
    assert augmented["root_candidates"][0]["props"]["stale_packet_ms"] == ["176503"]
    assert augmented["root_candidates"][0]["props"]["tool_use_id"] == "toolu_rich"
    assert augmented["evidence"][0]["command"] == "sf log sls query --query EXCEPTION_ERROR -f json"


def _run_payload(command: str, payload: object) -> dict[str, object]:
    return _run_payload_with_content(command, json.dumps(payload, ensure_ascii=False))


def _run_payload_with_content(command: str, content: str) -> dict[str, object]:
    return {
        "output": [
            {
                "event": "agent.tool_use",
                "data": {
                    "arguments": [
                        {
                            "id": "toolu_1",
                            "input": {"command": command},
                        }
                    ]
                },
            },
            {
                "event": "agent.tool_result",
                "data": {
                    "result": [
                        {
                            "tool_use_id": "toolu_1",
                            "content": content,
                        }
                    ]
                },
            },
        ]
    }


def _run_payload_pairs(pairs: list[tuple[str, str, object]]) -> dict[str, object]:
    return {
        "output": [
            {
                "event": "agent.tool_use",
                "data": {
                    "arguments": [
                        {"id": tool_use_id, "input": {"command": command}}
                        for tool_use_id, command, _payload in pairs
                    ]
                },
            },
            {
                "event": "agent.tool_result",
                "data": {
                    "result": [
                        {
                            "tool_use_id": tool_use_id,
                            "content": json.dumps(payload, ensure_ascii=False),
                        }
                        for tool_use_id, _command, payload in pairs
                    ]
                },
            },
        ]
    }
