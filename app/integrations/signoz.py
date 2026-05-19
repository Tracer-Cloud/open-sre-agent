"""SigNoz integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for SigNoz ClickHouse instances.  All operations are production-safe:
read-only, timeouts enforced, result sizes capped.

SigNoz stores logs, metrics, and traces in ClickHouse using predictable
schemas.  This module wraps the shared ClickHouse transport and adds
schema-aware validation and query helpers.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from app.integrations._validation_helpers import report_validation_failure
from app.integrations.clickhouse import ClickHouseConfig, _get_client
from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

DEFAULT_SIGNOZ_PORT = 8123
DEFAULT_SIGNOZ_DATABASE = "default"
DEFAULT_SIGNOZ_USER = "default"
DEFAULT_SIGNOZ_TIMEOUT_SECONDS = 10.0
DEFAULT_SIGNOZ_MAX_RESULTS = 50

REQUIRED_TABLES = (
    "signoz_logs.distributed_logs_v2",
    "signoz_metrics.distributed_samples_v4",
    "signoz_metrics.distributed_time_series_v4",
    "signoz_traces.distributed_signoz_index_v3",
)


class SigNozConfig(StrictConfigModel):
    """Normalized SigNoz connection settings.

    Credentials can come from dedicated ``SIGNOZ_*`` env vars or from the
    shared ClickHouse path when SigNoz is co-located with a generic
    ClickHouse instance.
    """

    url: str = ""
    api_key: str = ""
    clickhouse_host: str = ""
    clickhouse_port: int = DEFAULT_SIGNOZ_PORT
    clickhouse_database: str = DEFAULT_SIGNOZ_DATABASE
    clickhouse_user: str = DEFAULT_SIGNOZ_USER
    clickhouse_password: str = ""
    secure: bool = False
    timeout_seconds: float = Field(default=DEFAULT_SIGNOZ_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_SIGNOZ_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("clickhouse_host", mode="before")
    @classmethod
    def _normalize_host(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("clickhouse_database", mode="before")
    @classmethod
    def _normalize_database(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_SIGNOZ_DATABASE).strip()
        return normalized or DEFAULT_SIGNOZ_DATABASE

    @field_validator("clickhouse_user", mode="before")
    @classmethod
    def _normalize_user(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_SIGNOZ_USER).strip()
        return normalized or DEFAULT_SIGNOZ_USER

    @property
    def is_configured(self) -> bool:
        return bool(self.clickhouse_host)

    def to_clickhouse_config(self) -> ClickHouseConfig:
        """Project self into the generic ClickHouse config shape."""
        return ClickHouseConfig.model_validate(
            {
                "host": self.clickhouse_host,
                "port": self.clickhouse_port,
                "database": self.clickhouse_database,
                "username": self.clickhouse_user,
                "password": self.clickhouse_password,
                "secure": self.secure,
                "timeout_seconds": self.timeout_seconds,
                "max_results": self.max_results,
                "integration_id": self.integration_id,
            }
        )


@dataclass(frozen=True)
class SigNozValidationResult:
    """Result of validating a SigNoz integration."""

    ok: bool
    detail: str


def build_signoz_config(raw: dict[str, Any] | None) -> SigNozConfig:
    """Build a normalized SigNoz config object from env/store data."""
    return SigNozConfig.model_validate(raw or {})


def signoz_config_from_env() -> SigNozConfig | None:
    """Load a SigNoz config from env vars."""
    host = os.getenv("SIGNOZ_CLICKHOUSE_HOST", "").strip()
    if not host:
        return None
    return build_signoz_config(
        {
            "url": os.getenv("SIGNOZ_URL", "").strip(),
            "api_key": os.getenv("SIGNOZ_API_KEY", "").strip(),
            "clickhouse_host": host,
            "clickhouse_port": int(
                os.getenv("SIGNOZ_CLICKHOUSE_PORT", str(DEFAULT_SIGNOZ_PORT))
                or str(DEFAULT_SIGNOZ_PORT)
            ),
            "clickhouse_database": os.getenv(
                "SIGNOZ_CLICKHOUSE_DATABASE", DEFAULT_SIGNOZ_DATABASE
            ).strip(),
            "clickhouse_user": os.getenv("SIGNOZ_CLICKHOUSE_USER", DEFAULT_SIGNOZ_USER).strip(),
            "clickhouse_password": os.getenv("SIGNOZ_CLICKHOUSE_PASSWORD", "").strip(),
            "secure": os.getenv("SIGNOZ_CLICKHOUSE_SECURE", "false").strip().lower()
            in ("true", "1", "yes"),
        }
    )


def validate_signoz_config(config: SigNozConfig) -> SigNozValidationResult:
    """Validate SigNoz connectivity and schema presence."""
    if not config.clickhouse_host:
        return SigNozValidationResult(ok=False, detail="SigNoz ClickHouse host is required.")

    ch = config.to_clickhouse_config()
    try:
        client = _get_client(ch)
        try:
            # Ping
            result = client.query("SELECT version()")
            version = result.first_row[0] if result.row_count > 0 else "unknown"

            # Schema probe
            missing: list[str] = []
            for table in REQUIRED_TABLES:
                try:
                    exists_result = client.query(
                        f"SELECT count() FROM system.tables WHERE database || '.' || name = '{table}'"
                    )
                    if exists_result.first_row[0] == 0:
                        missing.append(table)
                except Exception:
                    missing.append(table)

            if missing:
                return SigNozValidationResult(
                    ok=False,
                    detail=(
                        f"Connected to ClickHouse {version}, "
                        f"but missing tables: {', '.join(missing)}."
                    ),
                )

            return SigNozValidationResult(
                ok=True,
                detail=(
                    f"Connected to ClickHouse {version}; "
                    f"SigNoz schema verified ({len(REQUIRED_TABLES)} tables present)."
                ),
            )
        finally:
            client.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="signoz",
            method="validate_signoz_config",
        )
        return SigNozValidationResult(ok=False, detail=f"SigNoz connection failed: {err}")


def signoz_is_available(sources: dict[str, dict]) -> bool:
    """Check if SigNoz integration params are present in available sources."""
    return bool(sources.get("signoz", {}).get("connection_verified"))


def signoz_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract SigNoz connection params from resolved integrations.

    Credentials are resolved from the integration store or environment, so the
    LLM never needs to supply host or password directly.
    """
    sz = sources.get("signoz", {})
    return {
        "clickhouse_host": str(sz.get("clickhouse_host", "")).strip(),
        "clickhouse_port": int(sz.get("clickhouse_port") or DEFAULT_SIGNOZ_PORT),
        "clickhouse_database": str(sz.get("clickhouse_database", DEFAULT_SIGNOZ_DATABASE)).strip(),
        "clickhouse_user": str(sz.get("clickhouse_user", DEFAULT_SIGNOZ_USER)).strip(),
        "clickhouse_password": str(sz.get("clickhouse_password", "")).strip(),
        "secure": bool(sz.get("secure", False)),
        "url": str(sz.get("url", "")).strip(),
        "api_key": str(sz.get("api_key", "")).strip(),
    }
