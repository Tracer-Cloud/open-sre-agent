"""Typed, versioned ORCA trial artifacts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ArtifactModel(BaseModel):
    """Strict base for persisted artifact schemas."""

    model_config = ConfigDict(extra="forbid")


class RunStatus(StrEnum):
    """Terminal state of the OpenSRE portion of an ORCA trial."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class UsageEvent(ArtifactModel):
    """One model call observed through OpenSRE's native usage hook."""

    sequence: int = Field(ge=1)
    requested_model: str
    response_model: str | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_creation_tokens: int | None = Field(default=None, ge=0)
    api_type: str | None = None
    max_output_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: str | None = None


class ModelCallAttemptEvent(ArtifactModel):
    """One provider request attempt, including failures that have no usage."""

    sequence: int = Field(ge=1)
    requested_model: str
    api_type: str
    attempt: int = Field(ge=1)
    status: Literal["succeeded", "failed"]
    duration_seconds: float = Field(ge=0)
    response_model: str | None = None
    response_id: str | None = None
    error_type: str | None = None


class ErrorRecord(ArtifactModel):
    """Redacted exception details retained when native execution fails."""

    category: str
    exception_type: str
    message: str
    traceback: str


class RunSummary(ArtifactModel):
    """Small host-facing result used to populate Harbor trial metadata."""

    schema_version: Literal[1] = 1
    status: Literal["succeeded"] = "succeeded"
    llm_calls: int = Field(ge=0, strict=True)
    input_tokens: int = Field(ge=0, strict=True)
    output_tokens: int = Field(ge=0, strict=True)
    cache_read_tokens: int = Field(default=0, ge=0, strict=True)
    cache_creation_tokens: int = Field(default=0, ge=0, strict=True)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunManifest(ArtifactModel):
    """Stable provenance and completion summary for one task trial."""

    schema_version: Literal[1] = 1
    profile: Literal["benchmark", "smoke"] = "benchmark"
    mode: Literal["native"] = "native"
    status: RunStatus
    integration_version: str
    opensre_commit: str
    dirty_files: tuple[str, ...]
    python_version: str
    model: str
    model_provider: str
    model_transport: str
    reasoning_effort: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    native_max_output_tokens: int = Field(ge=1)
    returned_models: tuple[str, ...] = ()
    instruction_sha256: str
    report_sha256: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_creation_tokens: int = Field(default=0, ge=0)
