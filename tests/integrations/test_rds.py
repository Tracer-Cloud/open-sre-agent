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
