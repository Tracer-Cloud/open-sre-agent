"""Validated configuration shared by the host-side Harbor agent and container runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1


class StrictFrozenModel(BaseModel):
    """Base model for immutable benchmark configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelSettings(StrictFrozenModel):
    """OpenSRE's native LLM route for this experiment."""

    harbor_model: str = "gradient_ai/openai-gpt-5.5"
    provider: Literal["openai"] = "openai"
    transport: Literal["sdk"] = "sdk"
    reasoning_effort: Literal["low", "medium", "high"] = "medium"

    @property
    def opensre_model(self) -> str:
        """Return the provider-facing model identifier used by OpenSRE."""
        prefix = "gradient_ai/"
        return self.harbor_model.removeprefix(prefix)

    @model_validator(mode="after")
    def validate_native_route(self) -> Self:
        """Reject routes that the first native implementation cannot represent."""
        model = self.opensre_model
        if not model or not model.startswith("openai-"):
            raise ValueError(
                "native ORCA mode currently requires an OpenAI model exposed by "
                "Gradient AI (for example gradient_ai/openai-gpt-5.5)"
            )
        return self


class GrafanaSettings(StrictFrozenModel):
    """Public credentials and connection policy from the ORCA task contract."""

    username: str = "admin"
    password: str = "admin"
    compatibility_token: str = "orca-basic-auth"
    verify_ssl: bool = True

    @model_validator(mode="after")
    def validate_connection(self) -> Self:
        """Require values needed by OpenSRE's current Grafana configuration predicate."""
        if not self.username or not self.password:
            raise ValueError("Grafana basic-auth username and password are required")
        if not self.compatibility_token:
            raise ValueError("Grafana compatibility_token must be nonempty")
        return self


class RuntimeSettings(StrictFrozenModel):
    """Paths and bounded waits inside the official ORCA task container."""

    report_path: Path = Path("/app/report.md")
    artifact_dir: Path = Path("/logs/agent/opensre-orca")
    environment_ready_path: Path = Path("/tmp/env-ready")
    environment_ports_path: Path = Path("/tmp/env-ports")
    readiness_timeout_seconds: int = Field(default=180, ge=1, le=600)
    grafana_timeout_seconds: int = Field(default=10, ge=1, le=60)

    @model_validator(mode="after")
    def validate_absolute_paths(self) -> Self:
        """Keep all container paths explicit and independent of the current directory."""
        for name in (
            "report_path",
            "artifact_dir",
            "environment_ready_path",
            "environment_ports_path",
        ):
            path = getattr(self, name)
            if not path.is_absolute():
                raise ValueError(f"{name} must be absolute: {path}")
        return self


class BenchmarkSettings(StrictFrozenModel):
    """Checked-in, secret-free settings for the one-task native experiment."""

    schema_version: Literal[1] = SCHEMA_VERSION
    mode: Literal["native"] = "native"
    model: ModelSettings = Field(default_factory=ModelSettings)
    grafana: GrafanaSettings = Field(default_factory=GrafanaSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)

    @classmethod
    def from_yaml(cls, path: Path) -> BenchmarkSettings:
        """Load and validate a checked-in YAML experiment definition."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"benchmark config must contain a YAML mapping: {path}")
        return cls.model_validate(raw)


class BuildManifest(StrictFrozenModel):
    """Identity and integrity metadata for an offline OpenSRE bundle."""

    schema_version: Literal[1] = SCHEMA_VERSION
    opensre_commit: str = Field(min_length=7)
    dirty_files: tuple[str, ...] = ()
    python_version: str
    opensre_wheel: str
    files_sha256: dict[str, str]

    @classmethod
    def from_path(cls, path: Path) -> BuildManifest:
        """Load a bundle manifest from JSON."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class RunnerSettings(StrictFrozenModel):
    """Complete non-secret configuration uploaded into one ORCA container."""

    schema_version: Literal[1] = SCHEMA_VERSION
    benchmark: BenchmarkSettings
    build: BuildManifest
    integration_version: str = "1"

    @classmethod
    def from_path(cls, path: Path) -> RunnerSettings:
        """Load the runner configuration uploaded by the Harbor agent."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def to_json(self) -> str:
        """Serialize deterministically for upload and provenance hashing."""
        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
