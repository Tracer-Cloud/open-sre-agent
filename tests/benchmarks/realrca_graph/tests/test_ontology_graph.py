from __future__ import annotations

from tests.benchmarks.realrca_graph.ontology_graph import OntologyGraph


def test_ontology_graph_indexes_nodes_and_edges() -> None:
    graph = OntologyGraph.from_context(
        {
            "nodes": [
                {"id": "app:consumer-app", "kind": "app", "label": "consumer-app"},
                {"id": "app:provider-app", "kind": "app", "label": "provider-app"},
                {
                    "id": "service:com.demo.ProviderApi@getThing",
                    "kind": "service",
                    "label": "com.demo.ProviderApi@getThing",
                },
            ],
            "edges": [
                {"source": "app:consumer-app", "rel": "CALLS", "target": "app:provider-app"},
                {
                    "source": "app:provider-app",
                    "rel": "EXPOSES",
                    "target": "service:com.demo.ProviderApi@getThing",
                },
            ],
        }
    )

    assert [node.id for node in graph.nodes_by_kind("app")] == [
        "app:consumer-app",
        "app:provider-app",
    ]
    assert len(graph.incident_edges("app:provider-app")) == 2
    assert graph.incident_edges("app:consumer-app", rel="CALLS")[0].target == "app:provider-app"


def test_ontology_graph_finds_nodes_for_answer_entities() -> None:
    graph = OntologyGraph.from_context(
        {
            "nodes": [
                {
                    "id": "service:com.demo.ProviderApi@getThing",
                    "kind": "service",
                    "label": "com.demo.ProviderApi:getThing",
                },
                {"id": "app:unrelated-app", "kind": "app", "label": "unrelated-app"},
            ],
            "edges": [],
        }
    )

    assert graph.node_ids_for_text("Root cause is com.demo.ProviderApi:getThing timeout.") == [
        "service:com.demo.ProviderApi@getThing"
    ]


def test_ontology_graph_scores_node_hits_from_partial_text() -> None:
    graph = OntologyGraph.from_context(
        {
            "nodes": [
                {
                    "id": "service:com.demo.ProviderApi@getThing",
                    "kind": "service",
                    "label": "com.demo.ProviderApi:getThing",
                },
                {"id": "app:unrelated-app", "kind": "app", "label": "unrelated-app"},
            ],
            "edges": [],
        }
    )

    hits = graph.node_hits_for_text("ProviderApi getThing is timing out.", kinds=["service"])

    assert hits
    assert hits[0].node.id == "service:com.demo.ProviderApi@getThing"
    assert hits[0].overlap >= 2


def test_ontology_graph_builds_bounded_neighborhood() -> None:
    graph = OntologyGraph.from_context(
        {
            "nodes": [
                {"id": "app:a", "kind": "app", "label": "a-app"},
                {"id": "app:b", "kind": "app", "label": "b-app"},
                {"id": "app:c", "kind": "app", "label": "c-app"},
            ],
            "edges": [
                {"source": "app:a", "rel": "CALLS", "target": "app:b"},
                {"source": "app:b", "rel": "CALLS", "target": "app:c"},
            ],
        }
    )

    one_hop = graph.neighborhood(["app:a"], depth=1)
    two_hop = graph.neighborhood(["app:a"], depth=2)

    assert one_hop.node_ids == ["app:a", "app:b"]
    assert two_hop.node_ids == ["app:a", "app:b", "app:c"]
    assert len(two_hop.edges) == 2


def test_ontology_graph_infers_endpoint_app_ownership_edges() -> None:
    graph = OntologyGraph.from_context(
        {
            "nodes": [
                {"id": "app:consumer-app", "kind": "app", "label": "consumer-app"},
                {
                    "id": "endpoint:consumer-app:host",
                    "kind": "endpoint",
                    "label": "consumer-app:default_host",
                },
                {
                    "id": "endpoint:https://example.com/api",
                    "kind": "endpoint",
                    "label": "https://example.com/api",
                },
            ],
            "edges": [],
        }
    )

    ownership_edges = graph.incident_edges("endpoint:consumer-app:host", rel="ENDPOINT_OF")
    assert len(ownership_edges) == 1
    assert ownership_edges[0].target == "app:consumer-app"
    assert graph.incident_edges("endpoint:https://example.com/api", rel="ENDPOINT_OF") == []
