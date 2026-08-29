from __future__ import annotations

from tests.benchmarks.realrca_graph.features import infer_root_layer
from tests.benchmarks.realrca_graph.topology import (
    topology_evidence_items,
    topology_root_candidates,
    trace_paths_from_context,
)


def _context() -> dict[str, object]:
    return {
        "nodes": [
            {"id": "trace:t1", "kind": "trace", "label": "t1"},
            {"id": "span:t1:0.1", "kind": "span", "label": "0.1", "props": {"duration_ms": 50}},
            {
                "id": "span:t1:0.1.2",
                "kind": "span",
                "label": "0.1.2",
                "props": {
                    "duration_ms": 3001,
                    "result_code": "03",
                    "raw": (
                        '{"server_ip":"33.42.114.145","host_ip":"33.42.114.145",'
                        '"server_name":"deep:host"}'
                    ),
                },
            },
            {"id": "endpoint:consumer:host", "kind": "endpoint", "label": "consumer:host"},
            {"id": "endpoint:provider:host", "kind": "endpoint", "label": "provider:host"},
            {"id": "endpoint:deep:host", "kind": "endpoint", "label": "deep:host"},
            {
                "id": "service:com.demo.ProviderApi@getThing",
                "kind": "service",
                "label": "com.demo.ProviderApi@getThing",
            },
            {
                "id": "service:com.demo.DeepApi@load",
                "kind": "service",
                "label": "com.demo.DeepApi@load",
            },
        ],
        "edges": [
            {"source": "trace:t1", "rel": "HAS_SPAN", "target": "span:t1:0.1"},
            {"source": "trace:t1", "rel": "HAS_SPAN", "target": "span:t1:0.1.2"},
            {"source": "span:t1:0.1", "rel": "CLIENT", "target": "endpoint:consumer:host"},
            {"source": "span:t1:0.1", "rel": "SERVER", "target": "endpoint:provider:host"},
            {
                "source": "span:t1:0.1",
                "rel": "INVOKES",
                "target": "service:com.demo.ProviderApi@getThing",
            },
            {"source": "span:t1:0.1.2", "rel": "CLIENT", "target": "endpoint:provider:host"},
            {"source": "span:t1:0.1.2", "rel": "SERVER", "target": "endpoint:deep:host"},
            {
                "source": "span:t1:0.1.2",
                "rel": "INVOKES",
                "target": "service:com.demo.DeepApi@load",
            },
        ],
    }


def test_trace_paths_include_slow_terminal_hop_and_ancestors() -> None:
    paths = trace_paths_from_context(_context())

    assert len(paths) == 1
    assert [hop.rpc_id for hop in paths[0].hops] == ["0.1", "0.1.2"]
    assert "consumer:host -> provider:host" in paths[0].summary()
    assert "provider:host -> deep:host" in paths[0].summary()
    assert "server_ip=33.42.114.145" in paths[0].summary()


def test_topology_evidence_items_use_dedicated_modality() -> None:
    items = topology_evidence_items(_context(), start_index=3)

    assert items[0].id == "e3"
    assert items[0].modality == "topology"
    assert "com.demo.DeepApi@load" in items[0].summary


def test_topology_root_candidates_target_terminal_slow_service() -> None:
    candidates = topology_root_candidates(_context())

    assert candidates[0]["kind"] == "topology_trace_path"
    assert candidates[0]["label"] == "com.demo.DeepApi@load"
    assert candidates[0]["props"]["trace_id"] == "t1"
    assert candidates[0]["props"]["server_ip"] == "33.42.114.145"


def test_topology_root_layer_is_service_dependency_even_with_host_labels() -> None:
    candidate = topology_root_candidates(_context())[0]

    assert (
        infer_root_layer(
            candidate["kind"],
            candidate["label"],
            candidate["props"],
            candidate["reason"],
        )
        == "service_dependency"
    )


def test_topology_root_candidates_skip_transport_entry_spans() -> None:
    context = _context()
    context["nodes"] = [
        item
        if not (isinstance(item, dict) and item.get("id") == "service:com.demo.DeepApi@load")
        else {
            "id": "service:https://example.test/path",
            "kind": "service",
            "label": "https://example.test/path",
        }
        for item in context["nodes"]
    ]
    context["edges"] = [
        item
        if not (
            isinstance(item, dict)
            and item.get("source") == "span:t1:0.1.2"
            and item.get("rel") == "INVOKES"
        )
        else {
            "source": "span:t1:0.1.2",
            "rel": "INVOKES",
            "target": "service:https://example.test/path",
        }
        for item in context["edges"]
    ]

    assert topology_root_candidates(context) == []
    assert topology_evidence_items(context)
