from __future__ import annotations

from app.tools.registry import get_registered_tool_map


def test_rds_performance_family_uses_rds_and_postgresql_contracts() -> None:
    tool_map = get_registered_tool_map("investigation")

    rds_tool = tool_map["describe_rds_instance"]
    pg_tool = tool_map["get_postgresql_slow_queries"]

    assert rds_tool.source == "rds"
    assert "db_instance_identifier" in set(rds_tool.public_input_schema.get("required", []))

    assert pg_tool.source == "postgresql"
    assert {"host", "threshold_ms"} <= set(pg_tool.public_input_schema.get("properties", {}).keys())


def test_kubernetes_contract_requires_cluster_and_namespace_filters() -> None:
    tool_map = get_registered_tool_map("investigation")
    eks_tool = tool_map["list_eks_pods"]
    required = set(eks_tool.public_input_schema.get("required", []))
    assert {"cluster_name", "namespace"} <= required


def test_metrics_contracts_hide_credentials_from_model_visible_schema() -> None:
    tool_map = get_registered_tool_map("investigation")
    grafana = tool_map["query_grafana_metrics"]
    datadog = tool_map["query_datadog_metrics"]

    grafana_props = set(grafana.public_input_schema.get("properties", {}).keys())
    datadog_props = set(datadog.public_input_schema.get("properties", {}).keys())

    assert {"grafana_endpoint", "grafana_api_key", "grafana_backend"}.isdisjoint(grafana_props)
    assert {"api_key", "app_key", "site"}.isdisjoint(datadog_props)
