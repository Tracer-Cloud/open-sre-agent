"""Tests for KafkaTopicHealthTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.KafkaTopicHealthTool import get_kafka_topic_health
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestKafkaTopicHealthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_kafka_topic_health.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = get_kafka_topic_health.__opensre_registered_tool__
    assert rt.is_available({"kafka": {"connection_verified": True}}) is True
    assert rt.is_available({"kafka": {"connection_verified": False}}) is False
    assert rt.is_available({"kafka": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_kafka_topic_health.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "kafka": {
                "connection_verified": True,
                "bootstrap_servers": "  broker:9092  ",
                "security_protocol": None,
                "sasl_mechanism": " PLAIN ",
                "sasl_username": " user ",
                "sasl_password": " pass ",
            }
        }
    )
    params = rt.extract_params(sources)
    assert params["bootstrap_servers"] == "broker:9092"
    assert params["security_protocol"] == "PLAINTEXT"
    assert params["sasl_mechanism"] == "PLAIN"
    assert params["sasl_username"] == "user"
    assert params["sasl_password"] == "pass"


def test_run_happy_path_calls_integration_helper() -> None:
    fake_response = {"source": "kafka", "available": True, "topics_returned": 1}
    with patch(
        "app.tools.KafkaTopicHealthTool.get_topic_health", return_value=fake_response
    ) as mock_get_topic_health:
        result = get_kafka_topic_health(
            bootstrap_servers="broker:9092",
            topic="my-topic",
            security_protocol="SASL_SSL",
            sasl_mechanism="PLAIN",
            sasl_username="alice",
            sasl_password="secret",
            limit=12,
        )

    assert result == fake_response

    (config,), kwargs = mock_get_topic_health.call_args
    assert config.bootstrap_servers == "broker:9092"
    assert config.security_protocol == "SASL_SSL"
    assert config.sasl_mechanism == "PLAIN"
    assert config.sasl_username == "alice"
    assert config.sasl_password == "secret"
    assert kwargs["topic"] == "my-topic"
    assert kwargs["limit"] == 12


def test_run_empty_topic_passes_none() -> None:
    fake_response = {"source": "kafka", "available": True, "topics_returned": 1}
    with patch(
        "app.tools.KafkaTopicHealthTool.get_topic_health", return_value=fake_response
    ) as mock_get_topic_health:
        result = get_kafka_topic_health(bootstrap_servers="broker:9092", topic="")

    assert result == fake_response
    (_,), kwargs = mock_get_topic_health.call_args
    assert kwargs["topic"] is None


def test_run_returns_error_dict_from_integration_helper() -> None:
    fake_error = {"source": "kafka", "available": False, "error": "boom"}
    with patch("app.tools.KafkaTopicHealthTool.get_topic_health", return_value=fake_error):
        result = get_kafka_topic_health(bootstrap_servers="broker:9092", topic="my-topic")

    assert result == fake_error
