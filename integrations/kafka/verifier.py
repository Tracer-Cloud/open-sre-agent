"""Kafka integration verifier.

The kafka integration module has heavy import-time side effects (mypy
configuration via a vendor SDK), so we lazy-import its config helpers
inside the wrappers below instead of importing at module top — same
pattern the monolith used before this migration.
"""

from __future__ import annotations

from typing import Any

from integrations.verification import register_validation_verifier


def _build_kafka_config(raw: dict[str, Any]) -> Any:
    from integrations.kafka import build_kafka_config

    return build_kafka_config(raw)


def _validate_kafka_config(config: Any) -> Any:
    from integrations.kafka import validate_kafka_config

    return validate_kafka_config(config)


verify_kafka = register_validation_verifier(
    "kafka",
    build_config=_build_kafka_config,
    validate_config=_validate_kafka_config,
)
