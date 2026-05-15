from tests.synthetic.rds_postgres.correlation.topology import (
    TopologyNode,
    score_topology_adjacency,
)


def test_topology_adjacency_scores_direct_upstream_dependency() -> None:
    web_tier = TopologyNode(
        name="orders-web-asg",
        node_type="ec2_asg",
        upstream_of=("orders-prod-mysql",),
    )

    rds = TopologyNode(
        name="orders-prod-mysql",
        node_type="rds_mysql",
        upstream_of=(),
    )

    result = score_topology_adjacency(
        source=web_tier,
        target=rds,
    )

    assert result.source == "orders-web-asg"
    assert result.target == "orders-prod-mysql"
    assert result.adjacency_score == 1.0


def test_topology_adjacency_scores_unrelated_tier_low() -> None:
    worker_tier = TopologyNode(
        name="orders-worker-asg",
        node_type="ec2_asg",
        upstream_of=("redis-cache",),
    )

    rds = TopologyNode(
        name="orders-prod-mysql",
        node_type="rds_mysql",
        upstream_of=(),
    )

    result = score_topology_adjacency(
        source=worker_tier,
        target=rds,
    )

    assert result.adjacency_score == 0.0
