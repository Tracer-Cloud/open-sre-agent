from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.benchmarks.orcabench.config import (
    BenchmarkSettings,
    ModelSettings,
    RuntimeSettings,
)


def _config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "configs/benchmark_one_task.yml"


def _smoke_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "configs/smoke_one_task.yml"


def test_host_config_import_does_not_eagerly_load_opensre_credentials() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import tests.benchmarks.orcabench.config; "
                "assert 'config.llm_auth.provider_catalog' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_checked_in_benchmark_config_uses_terminus_parity_and_explicit_model() -> None:
    settings = BenchmarkSettings.from_yaml(_config_path())

    assert settings.mode == "native"
    assert settings.tool_capability_mode == "terminus_parity"
    assert settings.profile == "benchmark"
    assert settings.model.harbor_model == "gradient_ai/openai-gpt-5.5"
    assert settings.model.opensre_model == "openai-gpt-5.5"
    assert settings.model.transport == "sdk"
    assert settings.model.reasoning_effort == "medium"
    assert settings.model.max_tokens == 16384
    assert settings.model.temperature == 1.0
    assert settings.runtime.source_root == Path("/app/opentelemetry-demo")
    assert settings.model.required_environment_names == (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    )
    assert settings.verifier.enabled


def test_smoke_config_is_unverified_and_defaults_to_gemini() -> None:
    settings = BenchmarkSettings.from_yaml(_smoke_config_path())

    assert settings.profile == "smoke"
    assert settings.tool_capability_mode == "terminus_parity"
    assert settings.model.harbor_model == "gemini/gemini-3.5-flash-lite"
    assert settings.model.opensre_model == "gemini-3.5-flash-lite"
    assert settings.model.max_tokens == 16384
    assert settings.model.reasoning_effort is None
    assert settings.model.required_environment_names == ("GEMINI_API_KEY",)
    assert not settings.verifier.enabled
    assert settings.verifier.required_environment_names == ()


@pytest.mark.parametrize(
    ("provider", "model", "harbor_model", "api_key_env"),
    [
        (
            "openrouter",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
            "OPENROUTER_API_KEY",
        ),
        ("nvidia", "z-ai/glm-5.2", "nvidia/z-ai/glm-5.2", "NVIDIA_API_KEY"),
        (
            "gemini",
            "gemini-3.5-flash-lite",
            "gemini/gemini-3.5-flash-lite",
            "GEMINI_API_KEY",
        ),
        (
            "groq",
            "openai/gpt-oss-120b",
            "groq/openai/gpt-oss-120b",
            "GROQ_API_KEY",
        ),
    ],
)
def test_runtime_model_override_preserves_smoke_policy(
    provider: str,
    model: str,
    harbor_model: str,
    api_key_env: str,
) -> None:
    settings = BenchmarkSettings.from_yaml(_smoke_config_path()).with_model_override(
        provider,
        model,
    )

    assert settings.profile == "smoke"
    assert settings.model.harbor_model == harbor_model
    assert settings.model.opensre_model == model
    assert settings.model.required_environment_names == (api_key_env,)
    assert not settings.verifier.enabled


@pytest.mark.parametrize(
    ("provider", "model"),
    [("groq", None), (None, "llama-3.3-70b-versatile")],
)
def test_runtime_model_override_requires_provider_and_model_together(
    provider: str | None,
    model: str | None,
) -> None:
    with pytest.raises(ValueError, match="must be supplied together"):
        BenchmarkSettings.from_yaml(_smoke_config_path()).with_model_override(
            provider,
            model,
        )


def test_model_settings_rejects_provider_outside_benchmark_allowlist() -> None:
    raw = BenchmarkSettings.from_yaml(_smoke_config_path()).model_dump()
    raw["model"]["provider"] = "deepseek"
    raw["model"]["harbor_model"] = "deepseek/deepseek-chat"

    with pytest.raises(ValueError, match="unsupported benchmark provider"):
        BenchmarkSettings.model_validate(raw)


def test_smoke_profile_rejects_enabled_verifier() -> None:
    raw = BenchmarkSettings().model_dump()
    raw["profile"] = "smoke"

    with pytest.raises(ValidationError, match="must disable ORCA verification"):
        BenchmarkSettings.model_validate(raw)


def test_openrouter_rejects_harbor_route_that_becomes_bare_model_name() -> None:
    with pytest.raises(ValidationError, match="owner/model"):
        ModelSettings(harbor_model="openrouter/free", provider="openrouter")


def test_config_rejects_unknown_fields() -> None:
    raw = BenchmarkSettings().model_dump()
    raw["unexpected"] = True

    with pytest.raises(ValidationError, match="unexpected"):
        BenchmarkSettings.model_validate(raw)


def test_config_rejects_unknown_tool_capability_mode() -> None:
    raw = BenchmarkSettings().model_dump()
    raw["tool_capability_mode"] = "shell"

    with pytest.raises(ValidationError, match="tool_capability_mode"):
        BenchmarkSettings.model_validate(raw)


def test_tool_capability_mode_override_is_validated() -> None:
    settings = BenchmarkSettings.from_yaml(_config_path()).with_tool_capability_mode_override(
        "terminus_parity",
    )

    assert settings.tool_capability_mode == "terminus_parity"


def test_tool_capability_mode_override_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError, match="tool_capability_mode"):
        BenchmarkSettings.from_yaml(_config_path()).with_tool_capability_mode_override(
            "shell",
        )


def test_runtime_paths_must_be_absolute() -> None:
    with pytest.raises(ValidationError, match="report_path must be absolute"):
        RuntimeSettings(report_path=Path("report.md"))
