from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.benchmarks.orcabench.config import BenchmarkSettings, RuntimeSettings


def _config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "configs/native_one_task.yml"


def _openrouter_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "configs/openrouter_smoke_one_task.yml"


def test_checked_in_config_is_native_and_model_route_is_explicit() -> None:
    settings = BenchmarkSettings.from_yaml(_config_path())

    assert settings.mode == "native"
    assert settings.profile == "benchmark"
    assert settings.model.harbor_model == "gradient_ai/openai-gpt-5.5"
    assert settings.model.opensre_model == "openai-gpt-5.5"
    assert settings.model.transport == "sdk"
    assert settings.model.reasoning_effort == "medium"
    assert settings.model.required_environment_names == (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    )
    assert settings.verifier.enabled


def test_openrouter_config_is_an_unverified_smoke_profile() -> None:
    settings = BenchmarkSettings.from_yaml(_openrouter_config_path())

    assert settings.profile == "smoke"
    assert settings.model.harbor_model == "openrouter/openrouter/free"
    assert settings.model.opensre_model == "openrouter/free"
    assert settings.model.required_environment_names == ("OPENROUTER_API_KEY",)
    assert not settings.verifier.enabled
    assert settings.verifier.required_environment_names == ()


def test_smoke_profile_rejects_enabled_verifier() -> None:
    raw = BenchmarkSettings().model_dump()
    raw["profile"] = "smoke"

    with pytest.raises(ValidationError, match="must disable ORCA verification"):
        BenchmarkSettings.model_validate(raw)


def test_config_rejects_unknown_fields() -> None:
    raw = BenchmarkSettings().model_dump()
    raw["unexpected"] = True

    with pytest.raises(ValidationError, match="unexpected"):
        BenchmarkSettings.model_validate(raw)


def test_runtime_paths_must_be_absolute() -> None:
    with pytest.raises(ValidationError, match="report_path must be absolute"):
        RuntimeSettings(report_path=Path("report.md"))
