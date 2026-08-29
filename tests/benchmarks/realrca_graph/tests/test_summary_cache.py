from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.summary_cache import compact_evidence_summary_cached


def test_summary_cache_reuses_compacted_raw_trace(tmp_path) -> None:
    raw_path = tmp_path / "trace.json"
    cache_dir = tmp_path / "cache"
    raw_path.write_text(
        json.dumps(
            [
                {
                    "clientName": "hotel-buy:hotel-buyhost",
                    "serverName": "tuan-item:tuan-item_default_production",
                    "service": "com.alibaba.demo.ProviderApi@getThing~P",
                    "duration": 748,
                    "resultModel": {"code": 3, "name": "TIMEOUT", "type": "TIMEOUT"},
                    "resultStr": "03",
                    "rpcTypeName": "HSF",
                    "serverIp": "33.5.100.72",
                    "hostIp": "33.3.251.222",
                }
            ]
        ),
        encoding="utf-8",
    )

    first = compact_evidence_summary_cached(
        "trace_get_abc",
        "sf trace get abc -f json",
        str(raw_path),
        "trace spans=0 top=",
        cache_dir=cache_dir,
    )
    second = compact_evidence_summary_cached(
        "trace_get_abc",
        "sf trace get abc -f json",
        str(raw_path),
        "trace spans=0 top=",
        cache_dir=cache_dir,
    )

    assert first == second
    assert "hsf_error_top=" in first
    assert "ProviderApi@getThing" in first
    assert list(cache_dir.glob("*/*.json"))


def test_summary_cache_uses_nonempty_fallback_when_raw_file_is_empty(tmp_path) -> None:
    raw_path = tmp_path / "sls_app.json"
    cache_dir = tmp_path / "cache"
    raw_path.write_text("[]", encoding="utf-8")
    fallback = (
        "app_logs count=30 top_signals=[kind=hsf_threadpool_busy "
        "label=THREADPOOL_BUSY:33.62.98.154 count=10]"
    )

    summary = compact_evidence_summary_cached(
        "sls_app_demo_THREADPOOL_BUSY",
        "sf log sls query --query THREADPOOL_BUSY -f json",
        str(raw_path),
        fallback,
        cache_dir=cache_dir,
    )

    assert "app_logs count=30" in summary
    assert "THREADPOOL_BUSY:33.62.98.154" in summary
