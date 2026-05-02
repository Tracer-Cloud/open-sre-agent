"""Tests for KafkaConsumerGroupTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.KafkaConsumerGroupTool import get_kafka_consumer_group_lag
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestKafkaConsumerGroupToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_kafka_consumer_group_lag.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = get_kafka_consumer_group_lag.__opensre_registered_tool__
    assert rt.is_available({"kafka": {"connection_verified": True}}) is True
    assert rt.is_available({"kafka": {"connection_verified": False}}) is False
    assert rt.is_available({"kafka": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_kafka_consumer_group_lag.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "kafka": {
                "connection_verified": True,
                "bootstrap_servers": "  broker:9092  ",
                "security_protocol": " sasl_ssl ",
                "sasl_mechanism": " PLAIN ",
                "sasl_username": " user ",
                "sasl_password": " pass ",
            }
        }
    )
    params = rt.extract_params(sources)
    assert params["bootstrap_servers"] == "broker:9092"
    assert params["security_protocol"] == "sasl_ssl"
    assert params["sasl_mechanism"] == "PLAIN"
    assert params["sasl_username"] == "user"
    assert params["sasl_password"] == "pass"


def test_run_happy_path_calls_integration_helper() -> None:
    fake_response = {
        "source": "kafka",
        "available": True,
        "group_id": "my-group",
        "partitions": [],
    }
    with patch(
        "app.tools.KafkaConsumerGroupTool.get_consumer_group_lag",
        return_value=fake_response,
    ) as mock_get_consumer_group_lag:
        result = get_kafka_consumer_group_lag(
            bootstrap_servers="broker:9092",
            group_id="my-group",
            security_protocol="SASL_SSL",
            sasl_mechanism="PLAIN",
            sasl_username="alice",
            sasl_password="secret",
        )

    assert result == fake_response

    (config,), kwargs = mock_get_consumer_group_lag.call_args
    assert config.bootstrap_servers == "broker:9092"
    assert config.security_protocol == "SASL_SSL"
    assert config.sasl_mechanism == "PLAIN"
    assert config.sasl_username == "alice"
    assert config.sasl_password == "secret"
    assert kwargs["group_id"] == "my-group"


def test_run_returns_error_dict_from_integration_helper() -> None:
    fake_error = {"source": "kafka", "available": False, "error": "boom"}
    with patch(
        "app.tools.KafkaConsumerGroupTool.get_consumer_group_lag",
        return_value=fake_error,
    ):
        result = get_kafka_consumer_group_lag(
            bootstrap_servers="broker:9092",
            group_id="my-group",
        )

    assert result == fake_error
