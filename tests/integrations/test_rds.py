"""Tests for integrations.rds helpers."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

from integrations.rds import (
    DEFAULT_RDS_REGION,
    RDSConfig,
    build_rds_config,
    rds_config_from_env,
    rds_extract_params,
    rds_is_available,
)
from integrations.rds.client import RDSClient
from integrations.rds.verifier import verify_rds
from integrations.registry import DIRECT_CLASSIFIED_EFFECTIVE_SERVICES, INTEGRATION_SPECS
from integrations.verification import list_verifiers


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
    assert params == {
        "db_instance_identifier": "prod-db",
        "region": "us-east-2",
        "aws_backend": None,
    }


def test_rds_extract_params_forwards_synthetic_backend_handle() -> None:
    """The fixture backend on ``sources['rds']['_backend']`` must reach the
    tool layer as ``aws_backend`` so RDS describe calls short-circuit instead
    of leaking to real boto3 during synthetic scenario runs.
    """
    sentinel = object()
    sources = {
        "rds": {
            "db_instance_identifier": "prod-db",
            "region": "us-east-1",
            "_backend": sentinel,
        }
    }
    params = rds_extract_params(sources)
    assert params["aws_backend"] is sentinel


def test_rds_is_available_true_with_backend_only() -> None:
    """Synthetic scenarios may carry only the injected ``_backend`` on the
    ``rds`` source slot — that alone must satisfy availability so the RDS
    tools remain selectable in synthetic mode.
    """
    sources = {"rds": {"_backend": object()}}
    assert rds_is_available(sources) is True


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


def test_load_env_integrations_skips_rds_when_db_id_missing() -> None:
    """Gap #1 — negative: with no RDS_DB_INSTANCE_IDENTIFIER, no rds record."""
    from integrations._catalog_impl import load_env_integrations

    with patch.dict(
        os.environ,
        {"AWS_REGION": "us-west-2", "OPENSRE_DISABLE_KEYRING": "1"},
        clear=True,
    ):
        env_records = load_env_integrations()

    assert not [r for r in env_records if r.get("service") == "rds"]


def test_classify_service_instance_rds_remote_store_returns_flat_shape() -> None:
    """Gap #2 — remote-store path: a stored RDS record must classify to a flat
    shape, not the generic {credentials: ...} fallback that broke rds_is_available."""
    from integrations._catalog_impl import _classify_service_instance

    credentials = {
        "db_instance_identifier": "remote-db",
        "region": "ap-southeast-2",
    }
    flat, resolved_key = _classify_service_instance("rds", credentials, record_id="store-record-42")

    assert resolved_key == "rds"
    assert flat is not None
    assert flat.db_instance_identifier == "remote-db"
    assert flat.region == "ap-southeast-2"
    assert flat.integration_id == "store-record-42"
    assert not hasattr(flat, "credentials"), (
        "remote-store rds must NOT nest fields under 'credentials' — "
        "rds_is_available reads sources['rds']['db_instance_identifier'] directly"
    )


def test_classify_service_instance_rds_skips_when_db_id_missing() -> None:
    """Gap #2 — negative: an unconfigured rds record must classify to (None, None)."""
    from integrations._catalog_impl import _classify_service_instance

    flat, resolved_key = _classify_service_instance(
        "rds", {"region": "us-east-1"}, record_id="incomplete"
    )

    assert flat is None and resolved_key is None


def _rds_client(identifier: str = "prod-orders-db", region: str = "us-east-1") -> RDSClient:
    return RDSClient(RDSConfig(db_instance_identifier=identifier, region=region))


def test_rds_spec_is_a_directly_effective_verified_integration() -> None:
    spec = next(item for item in INTEGRATION_SPECS if item.service == "rds")
    assert spec.has_verifier is True
    assert spec.direct_effective is True
    assert "rds" in DIRECT_CLASSIFIED_EFFECTIVE_SERVICES
    assert "rds" in list_verifiers()


def test_resolve_effective_integrations_publishes_store_configured_rds() -> None:
    from integrations.catalog import resolve_effective_integrations

    store_record = {
        "id": "rds-1",
        "service": "rds",
        "status": "active",
        "credentials": {
            "db_instance_identifier": "prod-orders-db",
            "region": "us-east-1",
        },
    }
    resolved = resolve_effective_integrations(store_integrations=[store_record])
    assert "rds" in resolved
    assert resolved["rds"]["source"] == "local store"
    assert resolved["rds"]["config"]["db_instance_identifier"] == "prod-orders-db"


def test_probe_access_missing_identifier() -> None:
    result = RDSClient(RDSConfig(db_instance_identifier="", region="us-east-1")).probe_access()
    assert result.status == "missing"
    assert "identifier" in result.detail.lower()


def test_probe_access_success() -> None:
    payload = {
        "success": True,
        "data": {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "prod-orders-db",
                    "DBInstanceStatus": "available",
                    "Engine": "postgres",
                }
            ]
        },
        "error": None,
    }
    with patch(
        "integrations.rds.client.execute_aws_sdk_call",
        return_value=payload,
    ) as mocked:
        result = _rds_client().probe_access()

    assert result.status == "passed"
    assert "prod-orders-db" in result.detail
    assert "available" in result.detail
    mocked.assert_called_once_with(
        service_name="rds",
        operation_name="describe_db_instances",
        parameters={"DBInstanceIdentifier": "prod-orders-db"},
        region="us-east-1",
    )


def test_probe_access_reports_api_error() -> None:
    with patch(
        "integrations.rds.client.execute_aws_sdk_call",
        return_value={
            "success": False,
            "error": "AWS API error (AccessDenied): not allowed",
            "data": None,
        },
    ):
        result = _rds_client().probe_access()

    assert result.status == "failed"
    assert "AccessDenied" in result.detail


def test_probe_access_reports_missing_instance() -> None:
    with patch(
        "integrations.rds.client.execute_aws_sdk_call",
        return_value={"success": True, "data": {"DBInstances": []}, "error": None},
    ):
        result = _rds_client().probe_access()

    assert result.status == "failed"
    assert "No RDS instance" in result.detail


def test_verify_rds_passed_on_describe_success() -> None:
    payload: dict[str, Any] = {
        "success": True,
        "data": {
            "DBInstances": [
                {"DBInstanceStatus": "available", "Engine": "postgres"},
            ]
        },
        "error": None,
    }
    with patch(
        "integrations.rds.client.execute_aws_sdk_call",
        return_value=payload,
    ):
        result = verify_rds(
            "local env",
            {"db_instance_identifier": "prod-orders-db", "region": "eu-west-1"},
        )

    assert result["service"] == "rds"
    assert result["status"] == "passed"
    assert "eu-west-1" in result["detail"]


def test_verify_rds_missing_when_identifier_blank() -> None:
    result = verify_rds("local store", {"db_instance_identifier": "", "region": "us-east-1"})
    assert result["status"] == "missing"
