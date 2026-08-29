import json
from pathlib import Path

from tests.benchmarks.realrca_graph.boundary_analysis import (
    _root_equivalence_category,
    build_boundary_delta_report,
    render_boundary_delta_markdown,
)


def test_boundary_delta_report_ranks_unprobed_root_layer_difference(tmp_path: Path) -> None:
    case_id = "case-321d"
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": case_id,
                        "diagnosis_output": "根因：downstream-s 的 QueryFacade.query 线程池打满导致上游超时。",
                        "trace_id": "2102f2dc17827131612854369d0ce1",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "test" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "test", "type": "HSF"},
                "evidence": [
                    {
                        "name": "event_changefree_query_downstream_s",
                        "summary": (
                            "events count=1 top=sourceProduct=CHANGEFREE_EXE "
                            "change_type=OFFLINE_HOST change_summary=downstream-s 应用变更 "
                            "change_app=downstream-s detail_url=https://n.alibaba-inc.com/"
                            "micro/ops/app/downstream-s/action/res/offline/detail id=1234567"
                        ),
                    },
                    {
                        "name": "metric_middleware_hsf_consumer_service_method_error_qps",
                        "summary": (
                            "metric=middleware_hsf_consumer_service_method_error_qps "
                            "top=[remote_app_name=downstream-s,method=QueryFacade.query,max=9,trend=rising]"
                        ),
                    },
                ],
                "root_candidates": [
                    {
                        "kind": "hsf_service_method",
                        "label": "downstream-s QueryFacade.query threadpool_busy",
                        "score": 4.0,
                        "reason": "threadpool_busy timeout",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_boundary_delta_report(
        baseline_path=baseline_path,
        graph_roots=[graph_root],
        split="test",
    )

    item = report.cases[0]
    assert item.case_suffix == "321d"
    assert item.baseline_matched_layer == "service_dependency"
    assert item.graph_top_layer == "change"
    assert "top_layer_diff:service_dependency->change" in item.categories
    assert "unprobed" in item.categories
    assert "root-boundary" in item.action_hint
    assert "Root-Boundary" in render_boundary_delta_markdown(report)


def test_boundary_delta_treats_cache_timeout_and_same_instance_as_equivalent() -> None:
    equivalence = _root_equivalence_category(
        matched_layer="cache",
        matched_label="cache_timeout",
        top_layer="cache",
        top_label="(tair@0b4d327051f446d7:tair.ldb.trip1a)",
        baseline_text=(
            "根因：fliggy-alime-gateway 到 Tair 实例 "
            "0b4d327051f446d7:tair.ldb.trip1a 写请求达到 50ms 超时。"
        ),
    )

    assert equivalence == "top_root_equiv:cache_instance"


def test_boundary_delta_treats_same_app_ip_hsf_methods_as_equivalent() -> None:
    equivalence = _root_equivalence_category(
        matched_layer="service_dependency",
        matched_label=(
            "union-seller-cpa OneDeliveryProjectReadService.query threadpool_busy@33.6.249.194"
        ),
        top_layer="service_dependency",
        top_label=(
            "union-seller-cpa KoxListReadService.queryKoxCountInEventByStatus "
            "threadpool_busy@33.6.249.194"
        ),
        baseline_text="",
    )

    assert equivalence == "top_root_equiv:same_app_ip"


def test_boundary_delta_treats_same_hsf_threadpool_host_across_layers_as_equivalent() -> None:
    equivalence = _root_equivalence_category(
        matched_layer="infrastructure",
        matched_label="fin-cif:fin-cif_hz_host@33.62.98.154",
        top_layer="service_dependency",
        top_label="THREADPOOL_BUSY:33.62.98.154",
        baseline_text=(
            "根因：下游 fin-cif 的单机 33.62.98.154 HSF Provider 业务线程池使用率突升至100%，"
            "导致 THREADPOOL_BUSY 拒绝及超时。"
        ),
    )

    assert equivalence == "top_root_equiv:hsf_threadpool_host"
