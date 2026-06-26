"""RabbitMQ integration verifier."""

from __future__ import annotations

from integrations.rabbitmq import build_rabbitmq_config, validate_rabbitmq_config
from integrations.verification import register_validation_verifier

verify_rabbitmq = register_validation_verifier(
    "rabbitmq",
    build_config=build_rabbitmq_config,
    validate_config=validate_rabbitmq_config,
)
