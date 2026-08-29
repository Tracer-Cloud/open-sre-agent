from __future__ import annotations

from tests.benchmarks.realrca_graph.access_logs import (
    access_log_search_terms,
    access_log_signals,
    should_query_access_logs,
    summarize_access_logs,
)
from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.features import infer_modality


def _rows() -> list[dict[str, object]]:
    return [
        {
            "logItem": {
                "status": "400",
                "request_method": "GET",
                "request_time_usec": "3144",
                "eagleeye_traceid": "2106d81117794303874833139e8571",
                "request_uri": (
                    "/gocBlockout/innerApi/v1/blockScope/batchGetNodeTree?"
                    "type=TPP_SCENE_ID&scopeKeys=1%2C2%2C3%2C4"
                ),
            },
            "sourceMeta": {"__source__": "33.44.245.165"},
        },
        {
            "logItem": {
                "status": "400",
                "request_method": "GET",
                "request_time_usec": "2284",
                "eagleeye_traceid": "2106d81117794303878983222e8571",
                "request_uri": (
                    "/gocBlockout/innerApi/v1/blockScope/batchGetNodeTree?"
                    "type=TPP_SCENE_ID&scopeKeys=1%2C2%2C3%2C4%2C5"
                ),
            },
            "sourceMeta": {"__source__": "33.50.242.198"},
        },
    ]


def test_should_query_access_logs_for_custom_monitor_alarm() -> None:
    assert should_query_access_logs(
        "自定义监控",
        {"title": "goc_pass_后端代理(nginx)", "content": "失败数 当前值为 18"},
    )


def test_access_log_search_terms_use_alarm_tags() -> None:
    terms = access_log_search_terms({"alarm_tags": [[{"name": "代理名", "value": "gocBlockout"}]]})

    assert terms == ["gocBlockout"]


def test_summarize_access_logs_compacts_status_path_and_param_count() -> None:
    summary = summarize_access_logs(_rows())

    assert "count=2" in summary
    assert "statuses={'400': 2}" in summary
    assert "/gocBlockout/innerApi/v1/blockScope/batchGetNodeTree" in summary
    assert "max_repeated_param_count=5" in summary


def test_access_log_signals_extract_http_error_root() -> None:
    signals = access_log_signals(_rows())

    assert signals[0].label == "http_400:/gocBlockout/innerApi/v1/blockScope/batchGetNodeTree"
    assert signals[0].props["status"] == "400"
    assert signals[0].props["max_repeated_param_count"] == 5
    assert signals[0].trace_ids == [
        "2106d81117794303874833139e8571",
        "2106d81117794303878983222e8571",
    ]


def test_access_log_signals_mark_401_as_auth_failure() -> None:
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

    signal = access_log_signals(rows)[0]

    assert signal.label == "http_401:/gocFaultDef/innerApi/v2/incident/scenarios/level/defs"
    assert signal.reason == "HTTP 401 access log authentication failure near alarm window"
    assert signal.props["auth_failure"] is True


def test_access_log_trace_ids_do_not_turn_sls_evidence_into_trace_modality() -> None:
    modality = infer_modality(
        "sls_access_goc_pass_nginx_gocBlockout",
        "sf log sls query --query gocBlockout -f json",
        "access_logs count=20 trace_ids=['2106d81117794303874833139e8571']",
    )

    assert modality == "log"


def test_sls_store_list_is_resource_metadata_not_log_evidence() -> None:
    modality = infer_modality(
        "sls_store_list",
        "sf log sls store list --app goc-pass -f json",
        "stores=[goc-pass-log:goc-pass-nginx]",
    )

    assert modality == "other"


def test_empty_access_log_results_do_not_support_http_candidate() -> None:
    bundle = build_evidence_bundle(
        {
            "case": {"case_id": "case-1", "split": "test", "type": "自定义监控"},
            "root_candidates": [
                {
                    "kind": "http_access_error",
                    "label": "http_400:/gocBlockout/innerApi/v1/blockScope/batchGetNodeTree",
                    "score": 5.0,
                    "reason": "HTTP access log error near alarm window",
                }
            ],
            "evidence": [
                {
                    "name": "sls_access_empty",
                    "command": "sf log sls query --query gocBlockout -f json",
                    "returncode": 0,
                    "summary": "access_logs count=0 top=",
                },
                {
                    "name": "sls_access_hit",
                    "command": "sf log sls query --query gocBlockout -f json",
                    "returncode": 0,
                    "summary": (
                        "access_logs count=20 statuses={'400': 9} "
                        "top_paths={'/gocBlockout/innerApi/v1/blockScope/batchGetNodeTree': 9}"
                    ),
                },
            ],
        }
    )

    support_names = [item.name for item in bundle.hypotheses[0].support]
    assert support_names == ["sls_access_hit"]
