from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.benchmarks.orcabench.config import BenchmarkSettings, RuntimeSettings


def _config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "configs/native_one_task.yml"


def test_checked_in_config_is_native_and_model_route_is_explicit() -> None:
    settings = BenchmarkSettings.from_yaml(_config_path())

    assert settings.mode == "native"
    assert settings.model.harbor_model == "gradient_ai/openai-gpt-5.5"
    assert settings.model.opensre_model == "openai-gpt-5.5"
    assert settings.model.transport == "sdk"
    assert settings.model.reasoning_effort == "medium"


def test_config_rejects_unknown_fields() -> None:
    raw = BenchmarkSettings().model_dump()
    raw["unexpected"] = True

    with pytest.raises(ValidationError, match="unexpected"):
        BenchmarkSettings.model_validate(raw)


def test_runtime_paths_must_be_absolute() -> None:
    with pytest.raises(ValidationError, match="report_path must be absolute"):
        RuntimeSettings(report_path=Path("report.md"))
