"""ClickHouse integration verifier (lazy-imported config helpers)."""

from __future__ import annotations

from typing import Any

from app.integrations.verification import register_validation_verifier


def _build_clickhouse_config(raw: dict[str, Any]) -> Any:
    from app.integrations.clickhouse import build_clickhouse_config

    return build_clickhouse_config(raw)


def _validate_clickhouse_config(config: Any) -> Any:
    from app.integrations.clickhouse import validate_clickhouse_config

    return validate_clickhouse_config(config)


verify_clickhouse = register_validation_verifier(
    "clickhouse",
    build_config=_build_clickhouse_config,
    validate_config=_validate_clickhouse_config,
)
