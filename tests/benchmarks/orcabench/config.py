"""Validated configuration shared by the host-side Harbor agent and container runner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ProviderRoute:
    """Names shared by Harbor, OpenSRE, and the host environment."""

    harbor_prefix: str
    environment_prefix: str
    api_key_env: str
    base_url_env: str | None = None


PROVIDER_ROUTES = {
    "openai": ProviderRoute(
        harbor_prefix="gradient_ai/",
        environment_prefix="OPENAI",
        api_key_env="OPENAI_API_KEY",
        base_url_env="OPENAI_BASE_URL",
    ),
    "openrouter": ProviderRoute(
        harbor_prefix="openrouter/",
        environment_prefix="OPENROUTER",
        api_key_env="OPENROUTER_API_KEY",
    ),
    "nvidia": ProviderRoute(
        harbor_prefix="nvidia/",
        environment_prefix="NVIDIA",
        api_key_env="NVIDIA_API_KEY",
    ),
}


class StrictFrozenModel(BaseModel):
    """Base model for immutable benchmark configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelSettings(StrictFrozenModel):
    """OpenSRE's native LLM route for this experiment."""

    harbor_model: str = "gradient_ai/openai-gpt-5.5"
    provider: Literal["openai", "openrouter", "nvidia"] = "openai"
    transport: Literal["sdk"] = "sdk"
    reasoning_effort: Literal["low", "medium", "high"] = "medium"
    max_tokens: int = Field(default=16384, ge=1)

    @property
    def opensre_model(self) -> str:
        """Return the provider-facing model identifier used by OpenSRE."""
        return self.harbor_model.removeprefix(self.route.harbor_prefix)

    @property
    def route(self) -> ProviderRoute:
        """Return the naming contract for the selected provider."""
        return PROVIDER_ROUTES[self.provider]

    @property
    def required_environment_names(self) -> tuple[str, ...]:
        """Return secret/config names that Harbor must pass to the agent."""
        names = [self.route.api_key_env]
        if self.route.base_url_env is not None:
            names.append(self.route.base_url_env)
        return tuple(names)

    @model_validator(mode="after")
    def validate_native_route(self) -> Self:
        """Reject routes that the first native implementation cannot represent."""
        if not self.harbor_model.startswith(self.route.harbor_prefix):
            raise ValueError(
                f"{self.provider} harbor_model must start with "
                f"{self.route.harbor_prefix!r}"
            )
        model = self.opensre_model
        if self.provider == "openai" and not model.startswith("openai-"):
            raise ValueError("Gradient AI route must identify an OpenAI model")
        if self.provider in {"openrouter", "nvidia"} and "/" not in model:
            raise ValueError(f"{self.provider} model must use an owner/model identifier")
        return self


class VerifierSettings(StrictFrozenModel):
    """ORCA verifier policy and its independently configured credentials."""

    enabled: bool = True
    api_key_env: str = "OPENAI_API_KEY"
    base_url_env: str = "OPENAI_BASE_URL"

    @property
    def required_environment_names(self) -> tuple[str, ...]:
        """Return verifier environment names only when verification is enabled."""
        if not self.enabled:
            return ()
        return (self.api_key_env, self.base_url_env)


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
    source_root: Path = Path("/app/opentelemetry-demo")
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
            "source_root",
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
    profile: Literal["benchmark", "smoke"] = "benchmark"
    mode: Literal["native"] = "native"
    model: ModelSettings = Field(default_factory=ModelSettings)
    verifier: VerifierSettings = Field(default_factory=VerifierSettings)
    grafana: GrafanaSettings = Field(default_factory=GrafanaSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        """Keep smoke runs visibly separate from scored benchmark runs."""
        if self.profile == "smoke" and self.verifier.enabled:
            raise ValueError("smoke profile must disable ORCA verification")
        return self

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
