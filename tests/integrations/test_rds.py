"""Tests for app.integrations.rds helpers."""

from __future__ import annotations

import os
from unittest.mock import patch

from app.integrations.rds import (
    DEFAULT_RDS_REGION,
    RDSConfig,
    build_rds_config,
    rds_config_from_env,
    rds_extract_params,
    rds_is_available,
)


def test_build_rds_config_with_data() -> None:
    config = build_rds_config({"db_instance_identifier": "prod-db", "region": "us-west-2"})
    assert isinstance(config, RDSConfig)
    assert config.db_instance_identifier == "prod-db"
    assert config.region == "us-west-2"
    assert config.is_configured is True


def test_build_rds_config_with_none_returns_empty() -> None:
    config = build_rds_config(None)
    assert config.db_instance_identifier == ""
    assert config.region == DEFAULT_RDS_REGION
    assert config.is_configured is False


def test_rds_config_from_env_returns_none_when_db_missing() -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert rds_config_from_env() is None


def test_rds_config_from_env_returns_config_when_set() -> None:
    env = {
        "RDS_DB_INSTANCE_IDENTIFIER": "staging-db",
        "AWS_REGION": "eu-west-1",
    }
    with patch.dict(os.environ, env, clear=True):
        config = rds_config_from_env()
        assert config is not None
        assert config.db_instance_identifier == "staging-db"
        assert config.region == "eu-west-1"


def test_rds_is_available_true_when_db_present() -> None:
    sources = {"rds": {"db_instance_identifier": "prod-db"}}
    assert rds_is_available(sources) is True


def test_rds_is_available_false_when_missing() -> None:
    assert rds_is_available({}) is False
    assert rds_is_available({"rds": {}}) is False


def test_rds_extract_params_returns_normalized_dict() -> None:
    sources = {"rds": {"db_instance_identifier": "  prod-db  ", "region": "  us-east-2 "}}
    params = rds_extract_params(sources)
    assert params == {"db_instance_identifier": "prod-db", "region": "us-east-2"}


def test_rds_extract_params_falls_back_to_env_region() -> None:
    sources = {"rds": {"db_instance_identifier": "prod-db"}}
    with patch.dict(os.environ, {"AWS_REGION": "ap-south-1"}, clear=True):
        params = rds_extract_params(sources)
        assert params["region"] == "ap-south-1"


def test_rds_extract_params_falls_back_to_rds_region_when_aws_region_unset() -> None:
    sources = {"rds": {"db_instance_identifier": "prod-db"}}
    with patch.dict(os.environ, {"RDS_REGION": "ca-central-1"}, clear=True):
        params = rds_extract_params(sources)
        assert params["region"] == "ca-central-1"


def test_rds_extract_params_defaults_when_no_env_or_source_region() -> None:
    sources = {"rds": {"db_instance_identifier": "prod-db"}}
    with patch.dict(os.environ, {}, clear=True):
        params = rds_extract_params(sources)
        assert params["region"] == DEFAULT_RDS_REGION


def test_rds_config_from_env_uses_rds_region_when_aws_region_unset() -> None:
    env = {
        "RDS_DB_INSTANCE_IDENTIFIER": "staging-db",
        "RDS_REGION": "ap-northeast-1",
    }
    with patch.dict(os.environ, env, clear=True):
        config = rds_config_from_env()
        assert config is not None
        assert config.region == "ap-northeast-1"


def test_rds_env_discovery_to_sources_pipeline() -> None:
    """Regression: env-set RDS config flows through env-discovery,
    catalog classification, and detect_sources into the sources dict."""
    from app.integrations._catalog_impl import (
        _classify_service_instance,
        load_env_integrations,
    )
    from app.nodes.plan_actions.detect_sources import detect_sources

    env = {
        "RDS_DB_INSTANCE_IDENTIFIER": "prod-orders-db",
        "AWS_REGION": "eu-west-1",
    }

    with patch.dict(os.environ, env, clear=True):
        env_records = load_env_integrations()

    rds_records = [r for r in env_records if r.get("service") == "rds"]
    assert len(rds_records) == 1, "load_env_integrations must register an rds record"
    record = rds_records[0]
    assert record["status"] == "active"
    assert record["credentials"]["db_instance_identifier"] == "prod-orders-db"
    assert record["credentials"]["region"] == "eu-west-1"

    flat, resolved_key = _classify_service_instance(
        "rds", record["credentials"], record_id=record["id"]
    )
    assert resolved_key == "rds"
    assert flat is not None
    assert flat["db_instance_identifier"] == "prod-orders-db"
    assert flat["region"] == "eu-west-1"
    assert "credentials" not in flat, "rds classifier must produce a flat shape"

    sources = detect_sources(
        raw_alert={},
        context={},
        resolved_integrations={"rds": flat},
    )
    assert "rds" in sources, "detect_sources must propagate rds into the sources dict"
    assert sources["rds"]["db_instance_identifier"] == "prod-orders-db"
    assert sources["rds"]["region"] == "eu-west-1"
