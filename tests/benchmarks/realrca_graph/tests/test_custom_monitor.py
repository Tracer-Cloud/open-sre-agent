from __future__ import annotations

from tests.benchmarks.realrca_graph.custom_monitor import (
    custom_monitor_metric_queries,
    custom_monitor_signal_label,
    custom_monitor_signal_score,
    extract_custom_monitor_ref,
    summarize_custom_monitor_fields,
    summarize_custom_monitor_spec,
)
from tests.benchmarks.realrca_graph.features import infer_modality, infer_root_layer


def _alarm() -> dict[str, object]:
    return {
        "app": "goc-pass",
        "metric": "1026_spm_19",
        "monitor_item_name": "goc_pass_后端代理(nginx)",
        "content": (
            "共有1条数据触发[critical]报警，摘要：\n"
            "* [gocBlockout] [失败数](https://x.alibaba-inc.com/custom/1026/product/"
            "preview/spm/19?crossTenant=true&%E4%BB%A3%E7%90%86%E5%90%8D=gocBlockout) "
            "[当前值为: 18] 最近3分钟连续大于10"
        ),
        "alarm_tags": [[{"name": "代理名", "value": "gocBlockout"}]],
    }


def test_extract_custom_monitor_ref_from_preview_url() -> None:
    ref = extract_custom_monitor_ref(_alarm())

    assert ref is not None
    assert ref.tenant_id == "1026"
    assert ref.plugin_type == "spm"
    assert ref.plugin_id == "19"
    assert ref.code == "1026_SPM_19"
    assert ref.url_dimensions["代理名"] == "gocBlockout"


def test_custom_monitor_metric_queries_use_fields_names_and_alarm_dimension() -> None:
    ref = extract_custom_monitor_ref(_alarm())
    assert ref is not None
    fields = {
        "name": "TR技术风险平台/goc-pass-前后端中间件/业务指标/goc_pass_后端代理(nginx)",
        "metricVOList": [
            {
                "displayName": "成功量",
                "name": "1026_SPM_19$成功量",
                "spaceAggregator": "sum",
                "dimensions": ["代理名"],
            },
            {
                "displayName": "失败数",
                "name": "1026_SPM_19$失败数",
                "spaceAggregator": "sum",
                "dimensions": ["代理名"],
            },
            {
                "displayName": "成功率",
                "name": "1026_SPM_19$成功率",
                "spaceAggregator": "avg",
                "dimensions": ["代理名"],
            },
        ],
    }

    queries = custom_monitor_metric_queries(fields, _alarm(), ref, limit=2)

    assert queries[0].display_name == "失败数"
    assert queries[0].query == 'sum(1026_SPM_19$失败数{代理名="gocBlockout"}) by (代理名)'
    assert queries[0].tenant == "sunfire_biz_juicer"
    assert queries[1].display_name == "成功率"
    assert queries[1].query == 'min(1026_SPM_19$成功率{代理名="gocBlockout"}) by (代理名)'


def test_custom_monitor_summary_and_signal_label_are_compact() -> None:
    ref = extract_custom_monitor_ref(_alarm())
    assert ref is not None
    fields_summary = summarize_custom_monitor_fields(
        {
            "name": "goc_pass_后端代理(nginx)",
            "metricVOList": [
                {"displayName": "失败数", "name": "1026_SPM_19$失败数", "dimensions": ["代理名"]}
            ],
            "aggViews": [{"dim": "all"}, {"dim": "app_group"}],
        },
        ref,
    )
    spec_summary = summarize_custom_monitor_spec(
        {
            "name": "goc_pass_后端代理(nginx)",
            "sourceType": "LOG",
            "log": {"path": "/home/admin/cai/logs/access_log", "apps": ["goc-pass"]},
            "groupBy": [{"dim": {"name": "代理名"}, "values": ["*"]}],
            "spm": {"resultDim": {"name": "code"}, "costDim": {"name": "rt"}},
            "whiteFilters": [{"dim": {"name": "proxyApi"}, "values": ["innerApi"]}],
        },
        ref,
    )

    assert "1026_SPM_19$失败数" in fields_summary
    assert "log_path=/home/admin/cai/logs/access_log" in spec_summary

    query = custom_monitor_metric_queries(
        {
            "metricVOList": [
                {
                    "displayName": "失败数",
                    "name": "1026_SPM_19$失败数",
                    "spaceAggregator": "sum",
                    "dimensions": ["代理名"],
                }
            ]
        },
        _alarm(),
        ref,
    )[0]
    label = custom_monitor_signal_label(ref, query, {"代理名": "gocBlockout", "__name__": "失败数"})
    score = custom_monitor_signal_score(
        query, {"代理名": "gocBlockout"}, {"max": 66, "trend": "rising"}
    )

    assert label == "1026_SPM_19:失败数:代理名=gocBlockout"
    assert score >= 4.5


def test_custom_monitor_signal_is_metric_application_evidence() -> None:
    assert infer_modality("custom_monitor_signal", "gocBlockout 失败数 rising") == "metric"
    assert (
        infer_root_layer(
            "custom_monitor_signal",
            "1026_SPM_19:失败数:代理名=gocBlockout",
            {},
            "custom monitor metric max=66 trend=rising",
        )
        == "application"
    )
