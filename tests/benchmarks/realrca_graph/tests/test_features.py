from __future__ import annotations

from tests.benchmarks.realrca_graph.features import (
    entity_features,
    infer_modality,
    keyword_features,
    token_features,
)


def test_keyword_features_do_not_match_rce_inside_resource() -> None:
    features = keyword_features("TDDL_QUERY@db:resource_lock_setting_his slow sql")

    assert "sql" in features
    assert "security" not in features


def test_keyword_features_match_standalone_rce_security_signal() -> None:
    features = keyword_features("heimdall detected standalone rce payload")

    assert "security" in features


def test_keyword_features_treat_downstream_interface_failure_as_timeout_family() -> None:
    features = keyword_features(
        "定位到调用下游alsc-saas-thirdgw应用的ThirdGwService.invoke接口失败"
    )

    assert "timeout" in features


def test_tddl_table_metric_exposes_sql_table_tokens() -> None:
    text = (
        "metric=middleware_tddl_write_table_rt series_count=41 "
        "top=[table=c2m_portrait_sku_map_product_sku_record "
        "max=56.9535,avg=10.8218,trend=rising]"
    )

    entities = entity_features(text)
    tokens = token_features(text)

    assert entities["sql_tables"] == ["c2m_portrait_sku_map_product_sku_record"]
    assert "sql_table:c2m_portrait_sku_map_product_sku_record" in tokens
    assert infer_modality("metric_middleware_tddl_write_table_rt", text) == "sql"
