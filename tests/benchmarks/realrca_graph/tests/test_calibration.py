from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.calibration import (
    build_calibration_report,
    render_calibration_markdown,
)


def test_build_calibration_report_scores_public_validation_truth(tmp_path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_id = "case-1"
    (dataset_dir / "validation.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "split": "validation",
                    "type": "HSF",
                    "data_ref": "snapshot",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (dataset_dir / "validation_ground_truth.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "root_cause_chain": [
                        {
                            "description": "provider-app ProviderApi timeout",
                            "component": {"name": "provider-app", "type": "app"},
                        }
                    ],
                    "reference": {
                        "required_items": [
                            {
                                "name": "provider timeout",
                                "description": "com.alibaba.demo.ProviderApi timeout",
                                "critical": True,
                            }
                        ]
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "validation" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "validation", "type": "HSF"},
                "root_candidates": [
                    {
                        "kind": "trace_span",
                        "label": "provider-app:provider_group",
                        "score": 5.0,
                        "reason": "com.alibaba.demo.ProviderApi timeout",
                    }
                ],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_calibration_report(graph_roots=[graph_root], dataset_dir=dataset_dir)

    assert report.case_count == 1
    assert report.top1_hit_rate == 1.0
    assert report.top3_hit_rate == 1.0
    assert report.cases[0].hypotheses[0].hit is True
    assert report.to_dict()["public_validation_truth_used"] is True


def test_calibration_counts_exact_sql_table_entity_as_critical_hit(tmp_path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_id = "case-table-rt"
    table = "c2m_portrait_sku_map_product_sku_record"
    (dataset_dir / "validation.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "split": "validation",
                    "type": "TDDL",
                    "data_ref": "snapshot",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (dataset_dir / "validation_ground_truth.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "root_cause_chain": [
                        {
                            "description": f"{table}表写入RT从正常水平飙升至约57ms",
                            "component": {"name": "disco-develop", "type": "app"},
                        }
                    ],
                    "reference": {
                        "required_items": [
                            {
                                "name": "写RT异常表定位",
                                "description": f"定位到disco-develop的{table}表写入RT飙升",
                                "critical": True,
                            }
                        ]
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "validation" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "validation", "type": "TDDL"},
                "root_candidates": [
                    {
                        "kind": "evidence_sql",
                        "label": table,
                        "score": 8.0,
                        "reason": "TDDL table write RT spike",
                        "props": {"sql_table": table},
                    }
                ],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_calibration_report(graph_roots=[graph_root], dataset_dir=dataset_dir)

    assert report.top1_hit_rate == 1.0
    assert report.cases[0].hypotheses[0].hit_critical_items == ["写RT异常表定位"]


def test_calibration_requires_critical_item_coverage_when_available(tmp_path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_id = "case-attack"
    (dataset_dir / "validation.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "split": "validation",
                    "type": "自定义监控",
                    "data_ref": "snapshot",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (dataset_dir / "validation_ground_truth.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "root_cause_chain": [
                        {
                            "description": "external security scan injects malicious bizType",
                            "component": {"name": "mtop", "type": "app"},
                        }
                    ],
                    "reference": {
                        "required_items": [
                            {
                                "name": "security scan payload",
                                "description": "SSRF RCE Fastjson malicious bizType payload",
                                "critical": True,
                            }
                        ]
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "validation" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "validation", "type": "自定义监控"},
                "root_candidates": [
                    {
                        "kind": "trace_span",
                        "label": "tmc-datacubehost->mtop:QnMobilePopService#get",
                        "score": 5.0,
                        "reason": "provider success rate dropped",
                    }
                ],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_calibration_report(graph_roots=[graph_root], dataset_dir=dataset_dir)

    hypothesis = report.cases[0].hypotheses[0]
    assert hypothesis.hit is False
    assert hypothesis.critical_item_coverage == 0.0
    assert hypothesis.missing_critical_items == ["security scan payload"]


def test_calibration_matches_security_mechanism_without_exact_cve_terms(tmp_path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_id = "case-attack"
    (dataset_dir / "validation.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "split": "validation",
                    "type": "自定义监控",
                    "data_ref": "snapshot",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (dataset_dir / "validation_ground_truth.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "root_cause_chain": [
                        {
                            "description": "外部攻击者通过mtop网关批量发送安全扫描请求",
                            "component": {"name": "mtop", "type": "app"},
                        }
                    ],
                    "reference": {
                        "required_items": [
                            {
                                "name": "外部攻击探测",
                                "description": "SSRF RCE Fastjson malicious bizType payload",
                                "critical": True,
                            }
                        ]
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "validation" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "validation", "type": "自定义监控"},
                "root_candidates": [
                    {
                        "kind": "pattern_security_scan",
                        "label": "mtop security_scan",
                        "score": 7.0,
                        "reason": (
                            "visible alarm/log text indicates malicious security scan payload: "
                            "heimdall=1 bx-x5action=break security-fourier"
                        ),
                    }
                ],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_calibration_report(graph_roots=[graph_root], dataset_dir=dataset_dir)

    hypothesis = report.cases[0].hypotheses[0]
    assert hypothesis.hit is True
    assert hypothesis.hit_critical_items == ["外部攻击探测"]


def test_calibration_matches_infra_hardware_event_mechanism(tmp_path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_id = "case-infra"
    (dataset_dir / "validation.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "split": "validation",
                    "type": "OTHER",
                    "data_ref": "snapshot",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (dataset_dir / "validation_ground_truth.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "root_cause_chain": [
                        {
                            "description": "ECS实例i-8vbiyp6wvmcp36j72a5u发生硬件内存故障",
                            "component": {"name": "ECS", "type": "infra"},
                        }
                    ],
                    "reference": {
                        "required_items": [
                            {
                                "name": "基础设施事件定位",
                                "description": "定位到ECS实例i-8vbiyp6wvmcp36j72a5u发生硬件内存故障",
                                "critical": True,
                            }
                        ]
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "validation" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "validation", "type": "OTHER"},
                "root_candidates": [
                    {
                        "kind": "pattern_infra_event",
                        "label": "i-8vbiyp6wvmcp36j72a5u hardware_memory_fault",
                        "score": 8.7,
                        "reason": "visible ECS event indicates host hardware or memory fault",
                    }
                ],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_calibration_report(graph_roots=[graph_root], dataset_dir=dataset_dir)

    hypothesis = report.cases[0].hypotheses[0]
    assert hypothesis.hit is True
    assert hypothesis.hit_critical_items == ["基础设施事件定位"]


def test_calibration_requires_shared_mechanism_for_mechanism_items(tmp_path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_id = "case-mq-cpu"
    (dataset_dir / "validation.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "split": "validation",
                    "type": "CPU",
                    "data_ref": "snapshot",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (dataset_dir / "validation_ground_truth.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "root_cause_chain": [
                        {
                            "description": "metaq topic demo_topic消息量激增导致CPU被打高",
                            "component": {"name": "demo-app", "type": "app"},
                        }
                    ],
                    "reference": {
                        "required_items": [
                            {
                                "name": "MQ消息激增致CPU打高",
                                "description": "metaq topic demo_topic message spike",
                                "critical": True,
                            }
                        ]
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "validation" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "validation", "type": "CPU"},
                "root_candidates": [
                    {
                        "kind": "pattern_host_anomaly",
                        "label": "33.1.2.3",
                        "score": 6.0,
                        "reason": "demo-app CPU was high on one host during the alarm window",
                    }
                ],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_calibration_report(graph_roots=[graph_root], dataset_dir=dataset_dir)

    hypothesis = report.cases[0].hypotheses[0]
    assert hypothesis.hit is False
    assert hypothesis.missing_critical_items == ["MQ消息激增致CPU打高"]


def test_calibration_matches_target_host_mechanism_with_single_machine_truth(tmp_path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_id = "case-host"
    (dataset_dir / "validation.json").write_text(
        json.dumps(
            [{"case_id": case_id, "split": "validation", "type": "HSF", "data_ref": "snapshot"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (dataset_dir / "validation_ground_truth.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "root_cause_chain": [
                        {
                            "description": "provider-app单机33.42.114.145发生故障",
                            "component": {"name": "provider-app", "type": "app"},
                        }
                    ],
                    "reference": {
                        "required_items": [
                            {
                                "name": "单机故障",
                                "description": "provider-app single machine 33.42.114.145",
                                "critical": True,
                            }
                        ]
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "validation" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "validation", "type": "HSF"},
                "root_candidates": [
                    {
                        "kind": "pattern_host_anomaly",
                        "label": "provider-app:provider-app_host@33.42.114.145",
                        "score": 7.0,
                        "reason": "single-host target-host server_ip=33.42.114.145 timed out",
                    }
                ],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_calibration_report(graph_roots=[graph_root], dataset_dir=dataset_dir)

    hypothesis = report.cases[0].hypotheses[0]
    assert hypothesis.hit is True
    assert hypothesis.hit_critical_items == ["单机故障"]


def test_calibration_does_not_use_support_to_change_hypothesis_mechanism(tmp_path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_id = "case-mq-support"
    (dataset_dir / "validation.json").write_text(
        json.dumps(
            [{"case_id": case_id, "split": "validation", "type": "CPU", "data_ref": "snapshot"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (dataset_dir / "validation_ground_truth.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "root_cause_chain": [
                        {
                            "description": "metaq topic demo_topic消息量激增导致CPU被打高",
                            "component": {"name": "demo-app", "type": "app"},
                        }
                    ],
                    "reference": {
                        "required_items": [
                            {
                                "name": "MQ消息激增致CPU打高",
                                "description": "metaq topic demo_topic message spike",
                                "critical": True,
                            }
                        ]
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "validation" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "validation", "type": "CPU"},
                "root_candidates": [
                    {
                        "kind": "metric_series",
                        "label": "pod_cpu_limit_usage:ip=33.1.2.3",
                        "score": 5.0,
                        "reason": "CPU metric on demo-app is high",
                    }
                ],
                "evidence": [
                    {
                        "name": "metric_middleware_metaq_clnt_receive_group_id_qps",
                        "summary": "topic=demo_topic group_id=demo-app max=10000 trend=rising",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_calibration_report(graph_roots=[graph_root], dataset_dir=dataset_dir)

    hypothesis = report.cases[0].hypotheses[0]
    assert hypothesis.hit is False


def test_calibration_matches_downstream_interface_failure_to_timeout_mechanism(tmp_path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_id = "case-downstream-interface-failure"
    (dataset_dir / "validation.json").write_text(
        json.dumps(
            [{"case_id": case_id, "split": "validation", "type": "HSF", "data_ref": "snapshot"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (dataset_dir / "validation_ground_truth.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case_id,
                    "root_cause_chain": [
                        {
                            "description": (
                                "定位到调用下游alsc-saas-thirdgw应用的ThirdGwService.invoke接口失败"
                            ),
                            "component": {"name": "alsc-saas-thirdgw", "type": "app"},
                        }
                    ],
                    "reference": {
                        "required_items": [
                            {
                                "name": "下游接口失败",
                                "description": (
                                    "定位到调用下游alsc-saas-thirdgw应用的ThirdGwService.invoke接口失败"
                                ),
                                "critical": True,
                            }
                        ]
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_root = tmp_path / "graphs"
    graph_path = graph_root / "validation" / case_id / "graph_context.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(
        json.dumps(
            {
                "case": {"case_id": case_id, "split": "validation", "type": "HSF"},
                "root_candidates": [
                    {
                        "kind": "pattern_hsf_downstream_timeout",
                        "label": "alsc-saas-thirdgw ThirdGwService.invoke downstream_timeout@33.103.98.250",
                        "score": 8.8,
                        "reason": (
                            "topology path shows alsc-saas-crm-groupon calling "
                            "alsc-saas-thirdgw ThirdGwService.invoke and timing out"
                        ),
                    }
                ],
                "evidence": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_calibration_report(graph_roots=[graph_root], dataset_dir=dataset_dir)

    hypothesis = report.cases[0].hypotheses[0]
    assert hypothesis.hit is True
    assert hypothesis.hit_critical_items == ["下游接口失败"]


def test_render_calibration_markdown_includes_rates(tmp_path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "validation.json").write_text("[]", encoding="utf-8")
    (dataset_dir / "validation_ground_truth.json").write_text("[]", encoding="utf-8")

    report = build_calibration_report(graph_roots=[tmp_path / "missing"], dataset_dir=dataset_dir)
    markdown = render_calibration_markdown(report)

    assert "top1_hit_rate" in markdown
    assert "hidden_test_reference_used" not in markdown
