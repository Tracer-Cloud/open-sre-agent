from __future__ import annotations

import json

from tests.benchmarks.realrca_graph import bundle_cache


def _graph_context(case_id: str, label: str) -> dict[str, object]:
    return {
        "case": {"case_id": case_id, "split": "test", "type": "HSF"},
        "root_candidates": [
            {
                "kind": "trace_span",
                "label": label,
                "score": 5.0,
                "reason": "abnormal HSF trace span",
                "props": {
                    "client": "consumer:consumer_group",
                    "server": label,
                    "service": "com.alibaba.demo.ProviderApi@getThing~P",
                    "result_code": "03",
                    "duration_ms": 10000,
                },
            }
        ],
        "evidence": [
            {
                "name": "trace_get",
                "command": "sf trace get abc -f json",
                "returncode": 0,
                "summary": f"{label} ProviderApi@getThing timeout",
            }
        ],
    }


def test_bundle_cache_reuses_bundle_payload(tmp_path, monkeypatch) -> None:
    graph_path = tmp_path / "graph_context.json"
    cache_dir = tmp_path / "cache"
    graph_path.write_text(
        json.dumps(_graph_context("case-1", "provider:provider_group")),
        encoding="utf-8",
    )

    first = bundle_cache.build_evidence_bundle_cached(graph_path, cache_dir=cache_dir)

    def _fail_if_rebuilt(*_args, **_kwargs):
        raise AssertionError("bundle cache was not reused")

    monkeypatch.setattr(bundle_cache, "build_evidence_bundle", _fail_if_rebuilt)
    second = bundle_cache.build_evidence_bundle_cached(graph_path, cache_dir=cache_dir)

    assert second.to_dict() == first.to_dict()
    assert list(cache_dir.glob("*/*.json"))


def test_bundle_cache_invalidates_when_graph_file_changes(tmp_path) -> None:
    graph_path = tmp_path / "graph_context.json"
    cache_dir = tmp_path / "cache"
    graph_path.write_text(
        json.dumps(_graph_context("case-1", "provider-one:provider_group")),
        encoding="utf-8",
    )
    first = bundle_cache.build_evidence_bundle_cached(graph_path, cache_dir=cache_dir)

    graph_path.write_text(
        json.dumps(_graph_context("case-1", "provider-two:provider_group-extra")),
        encoding="utf-8",
    )
    second = bundle_cache.build_evidence_bundle_cached(graph_path, cache_dir=cache_dir)

    assert first.hypotheses[0].label != second.hypotheses[0].label
